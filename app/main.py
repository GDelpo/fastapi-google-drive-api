"""Drive Service - Main application entry point.

This application is infrastructure-agnostic. It runs the same way:
- Locally on port 8084
- Behind Traefik with any path prefix
- Behind Nginx or any other reverse proxy
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import asyncio
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError as PydanticValidationError

from app.api import router as drive_router
from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.exceptions import (
    ServiceException,
    general_exception_handler,
    service_exception_handler,
    validation_exception_handler,
)
from app.logger import configure_logging, get_logger
from app.middleware import RequestLoggingMiddleware, ProxyHeadersMiddleware
from app.schemas import DependencyStatus, HealthResponse
from app.service import DriveService

# Configure logging before anything else
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: startup and shutdown events."""
    logger.info(
        "🚀 Starting Drive Service",
        extra={
            "extra_fields": {
                "version": settings.project_version,
                "environment": settings.environment,
                "debug": settings.debug,
            }
        },
    )

    # Initialize Drive service
    drive_service = DriveService()
    app_instance.state.drive_service = drive_service
    try:
        connected = await asyncio.wait_for(drive_service.test_connection(), timeout=5.0)
        if connected:
            logger.info("✅ Google Drive connection successful")
            app_instance.state.drive_connected = True
        else:
            logger.warning("⚠️ Google Drive connection failed — will retry in background")
            app_instance.state.drive_connected = False
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning("⚠️ Could not initialize Drive service: %s", e)
        app_instance.state.drive_connected = False

    # Create shared HTTP client for auth
    app_instance.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )

    # Pre-warm caches in background (non-blocking so the app starts serving immediately)
    async def _prewarm():
        try:
            logger.info("🔄 Pre-warming caches...")
            health_result, _, _ = await asyncio.gather(
                _probe_dependencies(drive_service),
                drive_service.get_stats(),
                drive_service.get_recent_files(),
                return_exceptions=True,
            )
            if isinstance(health_result, dict):
                _health_cache["data"] = health_result
                _health_cache["ts"] = time.monotonic()
            logger.info("✅ Caches pre-warmed")
        except Exception as e:
            logger.warning("⚠️ Cache pre-warm failed: %s", e)

    if getattr(app_instance.state, "drive_connected", False):
        asyncio.create_task(_prewarm())

    yield

    # Close shared HTTP client
    await app_instance.state.http_client.aclose()

    # Shutdown
    logger.info("👋 Shutting down Drive Service")


# Create FastAPI application
app = FastAPI(
    title=settings.project_name,
    description=settings.project_description,
    version=settings.project_version,
    lifespan=lifespan,
    # No root_path hardcoded - ProxyHeadersMiddleware reads X-Forwarded-Prefix
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# =============================================================================
# Middleware (order matters - first added = last executed, outermost first)
# =============================================================================

# Proxy headers middleware (outermost - handles X-Forwarded-Prefix for root_path)
app.add_middleware(ProxyHeadersMiddleware)

# Logging middleware
app.add_middleware(RequestLoggingMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# =============================================================================
# Exception Handlers
# =============================================================================

app.add_exception_handler(ServiceException, service_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(PydanticValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# =============================================================================
# Routes
# =============================================================================

# Include API router
app.include_router(drive_router, prefix=settings.api_prefix, tags=["drive"])

# Include Dashboard router
app.include_router(dashboard_router, prefix="/dashboard")


# Health check — cache results to avoid slow Google API calls on every probe

_health_cache: dict = {"data": None, "ts": 0.0}
_HEALTH_TTL = 30  # seconds — Traefik checks every 30s anyway
_health_lock = asyncio.Lock()


async def _probe_dependencies(drive_service: DriveService | None) -> dict[str, DependencyStatus]:
    """Run the actual dependency checks (slow, called at most every TTL)."""
    dependencies: dict[str, DependencyStatus] = {}

    if drive_service:
        try:
            t0 = time.monotonic()
            connected = await asyncio.wait_for(
                drive_service.test_connection(), timeout=10.0
            )
            latency = round((time.monotonic() - t0) * 1000, 1)
            if connected:
                dependencies["google_drive"] = DependencyStatus(
                    status="healthy", latency_ms=latency
                )
            else:
                # degraded (not unhealthy) — keeps Traefik serving traffic
                dependencies["google_drive"] = DependencyStatus(
                    status="degraded", error="Connection test failed"
                )
        except Exception as e:
            dependencies["google_drive"] = DependencyStatus(
                status="degraded", error=str(e)
            )
    else:
        dependencies["google_drive"] = DependencyStatus(
            status="degraded", error="Service not initialized"
        )

    return dependencies


def _get_auth_mode(request: Request) -> str:
    """Get the current Drive auth mode from the service instance."""
    drive_svc: DriveService | None = getattr(request.app.state, "drive_service", None)
    if drive_svc and getattr(drive_svc, "_auth_mode", "not_initialized") != "not_initialized":
        return drive_svc.auth_mode
    # Fallback: derive from config without triggering lazy init
    return "OAuth2 (cuenta personal)" if settings.use_oauth else "Service Account (15 GB)"


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(request: Request) -> HealthResponse:
    """Health check endpoint with cached dependency statuses.

    Dependency probes (Google Drive, Identity Service) are cached for 30 s
    so Traefik health probes respond instantly and never time out.
    """
    now = time.monotonic()

    if _health_cache["data"] is None or (now - _health_cache["ts"]) > _HEALTH_TTL:
        async with _health_lock:
            # Double-check after acquiring lock
            if _health_cache["data"] is None or (now - _health_cache["ts"]) > _HEALTH_TTL:
                drive_svc = getattr(request.app.state, "drive_service", None)
                _health_cache["data"] = await _probe_dependencies(drive_svc)
                _health_cache["ts"] = time.monotonic()

    dependencies = _health_cache["data"]

    # Overall status
    statuses = [d.status for d in dependencies.values()]
    if "unhealthy" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    response_data = HealthResponse(
        status=overall,
        version=settings.project_version,
        environment=settings.environment,
        auth_mode=_get_auth_mode(request),
        dependencies=dependencies,
    )

    # Always return 200 — Traefik health checks must pass to keep the
    # service in the load balancer.  The JSON body already indicates
    # the real status (healthy / degraded).
    return response_data


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8084,
        reload=settings.debug,
    )

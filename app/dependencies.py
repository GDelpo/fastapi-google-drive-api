"""FastAPI dependencies for the application."""

from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.auth import TokenData, get_current_user, service_role_required, admin_role_required
from app.service import DriveService


# ============================================================================
# Authentication Dependencies (re-exported from auth.py)
# ============================================================================

# These are the main auth dependencies to use in routes
CurrentUser = Annotated[TokenData, Depends(get_current_user)]
ServiceUser = Annotated[TokenData, Depends(service_role_required)]
AdminUser = Annotated[TokenData, Depends(admin_role_required)]


# ============================================================================
# HTTP Client Dependency
# ============================================================================

def get_http_client(request: Request) -> httpx.AsyncClient:
    """Get shared HTTP client from app state."""
    return request.app.state.http_client


# ============================================================================
# Service Dependencies (from app.state — created once in lifespan)
# ============================================================================


def get_drive_service(request: Request) -> DriveService:
    """Get Drive service from app state."""
    return request.app.state.drive_service


# Type alias for dependency injection
DriveServiceDep = Annotated[DriveService, Depends(get_drive_service)]

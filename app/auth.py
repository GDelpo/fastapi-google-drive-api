"""Authentication via Identity Service.

This service delegates authentication to the Identity Service.
It receives a Bearer token and validates it by calling the Identity Service's /me endpoint.
Results are cached in memory to avoid roundtrips a identity en cada request.
"""

import time
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.config import settings
from app.exceptions import AuthenticationError, AuthorizationError
from app.logger import get_logger

logger = get_logger(__name__)

_token_cache: dict[str, tuple["TokenData", float]] = {}
_TOKEN_CACHE_TTL = 300
_TOKEN_CACHE_MAX = 500


# OAuth2 scheme - tokenUrl points to identity service login (for Swagger UI)
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=settings.identity_login_url, 
    auto_error=not settings.skip_auth
)


class TokenData(BaseModel):
    """Token data from identity service."""

    id: str
    user_name: str
    mail: str | None = None
    role: str


# Development user for SKIP_AUTH mode
_dev_user = TokenData(id="dev-user", user_name="dev", mail="dev@local", role="service")


async def get_current_user(request: Request, token: str | None = Depends(oauth2_scheme)) -> TokenData:
    """Validate token with identity service and get user data.

    Args:
        request: FastAPI request (for shared HTTP client)
        token: Bearer token from Authorization header

    Returns:
        TokenData with user information

    Raises:
        AuthenticationError: If token validation fails
    """
    # Skip auth in development
    if settings.skip_auth:
        logger.warning("⚠️  SKIP_AUTH enabled - using dev user")
        return _dev_user

    if not token:
        raise AuthenticationError("Token required")

    cached = _token_cache.get(token)
    if cached:
        token_data, expiry = cached
        if time.monotonic() < expiry:
            return token_data
        del _token_cache[token]

    client = request.app.state.http_client
    try:
        response = await client.get(
            settings.identity_me_url,
            headers={"Authorization": f"Bearer {token}"},
        )

        if response.status_code == 200:
            data = response.json()
            token_data = TokenData(
                id=str(data["id"]),
                user_name=data["userName"],
                mail=data.get("mail"),
                role=data["role"],
            )
            if len(_token_cache) >= _TOKEN_CACHE_MAX:
                _token_cache.clear()
            _token_cache[token] = (token_data, time.monotonic() + _TOKEN_CACHE_TTL)
            return token_data

        # Token invalid or expired
        try:
            error_msg = response.json().get("detail", "Token validation failed")
        except Exception:
            error_msg = f"Identity service returned {response.status_code}"
        raise AuthenticationError(error_msg)

    except httpx.ConnectTimeout:
        logger.error("Identity service connection timeout")
        raise AuthenticationError("Identity service unavailable (connection timeout)")
    except httpx.ReadTimeout:
        logger.error("Identity service read timeout")
        raise AuthenticationError("Identity service unavailable (read timeout)")
    except httpx.ConnectError:
        logger.error("Cannot connect to identity service")
        raise AuthenticationError("Identity service unavailable")
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error("Identity service error: %s", e)
        raise AuthenticationError(str(e))


async def service_role_required(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require service or admin privileges.

    Accepts both 'service' (machine-to-machine from other microservices)
    and 'admin' (human operators via dashboard).
    """
    if current_user.role not in ("admin", "service"):
        raise AuthorizationError("Service or admin account required")
    return current_user


async def admin_role_required(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Require admin privileges."""
    if current_user.role not in ["admin", "service"]:
        raise AuthorizationError("Admin privileges required")
    return current_user

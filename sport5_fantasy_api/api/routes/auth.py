"""
Authentication routes for the Sport5 Fantasy API.

Endpoints
---------
POST /{tournament}/auth/login
    Authenticates a user with email and password against the Sport5 backend,
    extracts the ``.AspNetCore.Cookies`` session token, and returns a
    ``TokenResponse``.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sport5_fantasy_api.api.dependencies import resolve_connector
from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import Sport5AuthError
from sport5_fantasy_api.core.rate_limit import login_rate_limiter
from sport5_fantasy_api.models.auth import LoginRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Auth"])


@router.post(
    "/{tournament}/auth/login",
    response_model=TokenResponse,
    summary="Login to Sport5 Fantasy",
    description=(
        "Authenticates against the Sport5 backend using user credentials "
        "(email and password). Upon successful login, extracts and returns the "
        "``.AspNetCore.Cookies`` session token which can then be used as a "
        "Bearer token (or ``X-Sport5-Session`` header) for private endpoints."
    ),
    response_description="Access token response containing the session cookie value.",
)
async def login(
    request: LoginRequest,
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
    http_request: Request,
) -> TokenResponse:
    """
    Authenticate user credentials against Sport5 and return a session token.

    Protected by sliding-window rate limiting per client IP.
    """
    if settings.rate_limit_enabled:
        client_ip = http_request.client.host if http_request.client else "unknown"
        allowed, retry_after = await login_rate_limiter.is_allowed(client_ip)
        if not allowed:
            wait_seconds = max(1, int(retry_after))
            logger.warning(
                "[%s] Rate limit exceeded on login for client %s. Retry after %ds",
                connector.tournament_type,
                client_ip,
                wait_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many login attempts. Please retry after {wait_seconds} seconds.",
                headers={"Retry-After": str(wait_seconds)},
            )

    try:
        token = await connector.login(request.email, request.password)
    except Sport5AuthError as exc:
        masked_email = (
            request.email.split("@")[0][:2] + "***@" + request.email.split("@")[-1]
            if "@" in request.email
            else "***"
        )
        logger.warning(
            "[%s] Login failed for user %s: %s",
            connector.tournament_type,
            masked_email,
            exc.message,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
        ) from exc

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        tournament=connector.tournament_type,
    )

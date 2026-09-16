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

from fastapi import APIRouter, Depends, HTTPException, status

from sport5_fantasy_api.api.dependencies import resolve_connector
from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.core.exceptions import Sport5AuthError
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
) -> TokenResponse:
    """
    Authenticate user credentials against Sport5 and return a session token.

    Parameters
    ----------
    request:
        User email and password.
    connector:
        Connector resolved for the target tournament.

    Returns
    -------
    TokenResponse
        Extracted access token and tournament metadata.

    Raises
    ------
    HTTPException(401)
        If credentials are invalid or login is rejected by upstream.
    """
    try:
        token = await connector.login(request.email, request.password)
    except Sport5AuthError as exc:
        logger.warning(
            "[%s] Login failed for user %r: %s",
            connector.tournament_type,
            request.email,
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

"""
FastAPI dependency functions for the Sport5 Fantasy API.

Provides:
1. **Connector injection** (``resolve_connector``):
   Resolves the appropriate ``BaseSport5Connector`` from the
   ``ConnectorRegistry`` based on the ``{tournament}`` path parameter.
   Raises HTTP 404 if the tournament type is unknown.

2. **Unified authentication extraction** (``get_session_token``):
   Extracts the raw ``.AspNetCore.Cookies`` token from either:
   - ``Authorization: Bearer <token>`` (registered with OpenAPI for Swagger UI padlock)
   - Fallback: ``X-Sport5-Session: <token>``
   Raises HTTP 401 if neither is supplied or token is empty.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.connectors.registry import registry
from sport5_fantasy_api.core.exceptions import ConnectorNotFoundError
from sport5_fantasy_api.models.enums import TournamentType

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Bearer token containing the Sport5 .AspNetCore.Cookies session value.",
)


async def resolve_connector(
    tournament: TournamentType = Path(
        ...,
        description=(
            "Tournament slug identifying which Sport5 competition to query. "
            "Supported values: 'israel', 'champions', 'euroleague', 'world-cup', 'euro'."
        ),
        examples=[TournamentType.ISRAELI_LEAGUE],
    ),
) -> BaseSport5Connector:
    """
    FastAPI dependency: resolve the appropriate Sport5 connector.

    Parameters
    ----------
    tournament:
        The tournament type extracted from the URL path parameter.

    Returns
    -------
    BaseSport5Connector
        The singleton connector for the requested tournament.

    Raises
    ------
    HTTPException(404)
        When no connector is registered for the given tournament type.
    """
    try:
        return registry.get_connector(tournament)
    except ConnectorNotFoundError as exc:
        logger.warning("Connector resolution failed for tournament=%r", tournament)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Tournament {tournament!r} is not currently supported. "
                f"Supported values: {[t.value for t in TournamentType]}."
            ),
        ) from exc


async def get_session_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ] = None,
    x_sport5_session: Annotated[
        str | None,
        Header(
            alias="X-Sport5-Session",
            description=(
                "Raw .AspNetCore.Cookies session token for Sport5 authentication. "
                "Alternative to Authorization Bearer header."
            ),
        ),
    ] = None,
) -> str:
    """
    FastAPI dependency: extract and validate the Sport5 session token.

    Supports two mechanisms:
    1. HTTP Bearer authorization header: ``Authorization: Bearer <token>``
    2. Fallback custom header: ``X-Sport5-Session: <token>``

    Parameters
    ----------
    credentials:
        Parsed HTTPBearer authorization credentials (if provided).
    x_sport5_session:
        Optional fallback header containing the raw token.

    Returns
    -------
    str
        The stripped session token string.

    Raises
    ------
    HTTPException(401)
        If neither header is provided or if the token value is empty/whitespace.
    """
    token: str | None = None
    if credentials is not None and credentials.credentials:
        token = credentials.credentials.strip()
    elif x_sport5_session is not None and x_sport5_session.strip():
        token = x_sport5_session.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Authentication credentials missing. "
                "Provide Authorization Bearer token or X-Sport5-Session header."
            ),
        )
    return token


# Backward-compatibility alias
get_auth_cookie = get_session_token

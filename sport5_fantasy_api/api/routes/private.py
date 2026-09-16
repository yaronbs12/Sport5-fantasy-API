"""
Private (authenticated) API routes for the Sport5 Fantasy API.

All routes require authentication via either:
- ``Authorization: Bearer <token>``
- ``X-Sport5-Session: <token>``

where ``<token>`` is a valid ``.AspNetCore.Cookies`` token.
These responses are **never** globally cached as they are user-specific.

Endpoints
---------
GET /{tournament}/me/team
    Returns the authenticated user's squad lineup (starters, bench,
    captain, sub-captain) and remaining transfer budget.

GET /{tournament}/me/leagues
    Returns a summary list of all custom leagues the user belongs to.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from sport5_fantasy_api.api.dependencies import get_session_token, resolve_connector
from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.models.league import LeagueLeaderboard, LeagueSummary
from sport5_fantasy_api.models.user import UserTeamResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Private (Authenticated)"])


# ---------------------------------------------------------------------------
# My team endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/me/team",
    response_model=UserTeamResponse,
    summary="Get my fantasy squad",
    description=(
        "Returns the full squad for the authenticated user, including starting "
        "lineup, bench players, captain, and vice-captain. "
        "Requires Bearer token or ``X-Sport5-Session`` header.\n\n"
        "Pass optional ``user_id`` to inspect another user's squad.\n\n"
        "**This response is never globally cached** — every request hits the "
        "Sport5 upstream directly to ensure up-to-date lineup data."
    ),
    response_description="Full squad response with starters, bench, and captain data.",
)
async def get_my_team(
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
    session_token: Annotated[str, Depends(get_session_token)],
    user_id: Annotated[
        str | None,
        Query(
            description=(
                "Optional Sport5 user ID. When omitted, returns the squad of "
                "the session cookie owner."
            )
        ),
    ] = None,
) -> UserTeamResponse:
    """
    Return the authenticated user's fantasy squad (or target user_id squad).

    The session cookie is forwarded verbatim to the Sport5 upstream as
    ``Cookie: .AspNetCore.Cookies={token}``.
    """
    if user_id is not None:
        result = await connector.get_user_team(auth_cookie=session_token, user_id=user_id)
    else:
        result = await connector.get_user_team(auth_cookie=session_token)
    logger.info(
        "[%s] /me/team resolved for user_id=%r",
        connector.tournament_type,
        result.user_id,
    )
    return result


# ---------------------------------------------------------------------------
# My leagues endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/me/leagues",
    response_model=list[LeagueSummary],
    summary="Get my custom leagues",
    description=(
        "Returns a summary of all custom leagues the authenticated user has "
        "joined in the current season. "
        "Requires Bearer token or ``X-Sport5-Session`` header.\n\n"
        "**This response is never globally cached.**"
    ),
    response_description=(
        "List of leagues the authenticated user belongs to, "
        "including league ID, name, and member count."
    ),
)
async def get_my_leagues(
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
    session_token: Annotated[str, Depends(get_session_token)],
) -> list[LeagueSummary]:
    """
    Return all leagues the authenticated user belongs to.

    The session cookie is forwarded verbatim to the Sport5 upstream.
    """
    leagues = await connector.get_user_leagues(auth_cookie=session_token)
    logger.info(
        "[%s] /me/leagues returned %d leagues",
        connector.tournament_type,
        len(leagues),
    )
    return leagues


# ---------------------------------------------------------------------------
# League leaderboard endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/me/leagues/{league_id}",
    response_model=LeagueLeaderboard,
    summary="Get custom league standings",
    description=(
        "Returns paginated leaderboard rankings, scores, and team names for a specific "
        "custom league. Requires Bearer token or ``X-Sport5-Session`` header.\n\n"
        "**This response is never globally cached.**"
    ),
    response_description="Paginated league leaderboard with user rankings.",
)
async def get_league_standings(
    league_id: int,
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
    session_token: Annotated[str, Depends(get_session_token)],
    page: Annotated[
        int,
        Query(ge=0, description="0-based pagination page index (default: 0)."),
    ] = 0,
) -> LeagueLeaderboard:
    """
    Return paginated leaderboard rankings for a custom league.

    The session cookie is forwarded verbatim to the Sport5 upstream.
    """
    leaderboard = await connector.get_league_leaderboard(
        auth_cookie=session_token,
        league_id=league_id,
        page_index=page,
    )
    logger.info(
        "[%s] /me/leagues/%d resolved (page=%d, members=%d)",
        connector.tournament_type,
        league_id,
        page,
        len(leaderboard.members),
    )
    return leaderboard

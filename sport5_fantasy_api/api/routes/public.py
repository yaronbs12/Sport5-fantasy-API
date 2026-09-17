"""
Public (unauthenticated) API routes for the Sport5 Fantasy API.

All routes are prefixed with ``/api/v1/{tournament}/`` where ``{tournament}``
is a ``TournamentType`` slug (e.g. ``israel``, ``champions``, ``euroleague``).

Endpoints
---------
GET /tournaments
    Returns a directory of all supported tournaments with active status,
    sport type, and current season ID.

GET /{tournament}/players
    Returns the full player pool, with optional filters:
      - ``position``        (GK | DEF | MID | FWD)
      - ``max_price``       (float, inclusive upper bound)
      - ``team_id``         (int, filter by club team)
      - ``include_inactive``(bool, default False — omit inactive players)

GET /{tournament}/teams
    Returns a list of ``Team`` objects (id, name, logo_url, shirt_url).

GET /{tournament}/fixtures
    Returns ``LeagueMetaResponse`` — full season metadata including:
    current round, transfer deadline, all rounds, and all match fixtures.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from sport5_fantasy_api.api.dependencies import resolve_connector
from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.models.enums import Position, TournamentType
from sport5_fantasy_api.models.fixture import LeagueMetaResponse, Team
from sport5_fantasy_api.models.player import Player

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Public"])


# ---------------------------------------------------------------------------
# Tournament directory — no {tournament} path param
# ---------------------------------------------------------------------------


class TournamentInfo(BaseModel):
    """Metadata for a single supported tournament."""

    tournament: TournamentType
    name: str
    sport: str
    active: bool
    default_season_id: int
    url_name: str


# Verified upstream league directory (from GET /api/Leagues/GetLeagues)
_TOURNAMENT_DIRECTORY: list[TournamentInfo] = [
    TournamentInfo(
        tournament=TournamentType.ISRAELI_LEAGUE,
        name="ליגת העל — Israeli Premier League",
        sport="Football",
        active=True,
        default_season_id=10,
        url_name="dreamteam",
    ),
    TournamentInfo(
        tournament=TournamentType.CHAMPIONS_LEAGUE,
        name="Fantasy League — UEFA Champions League",
        sport="Football",
        active=True,
        default_season_id=12,
        url_name="fantasyleague",
    ),
    TournamentInfo(
        tournament=TournamentType.EUROLEAGUE,
        name="יורוליג — Euroleague Basketball",
        sport="Basketball",
        active=True,
        default_season_id=11,
        url_name="fantasyeuroleague",
    ),
    TournamentInfo(
        tournament=TournamentType.WORLD_CUP,
        name="מונדיאל — World Cup",
        sport="Football",
        active=True,
        default_season_id=9,
        url_name="fantasywc",
    ),
    TournamentInfo(
        tournament=TournamentType.EURO,
        name="Euro Fantasy",
        sport="Football",
        active=False,
        default_season_id=3,
        url_name="eurofantasy",
    ),
]


@router.get(
    "/tournaments",
    response_model=list[TournamentInfo],
    summary="List all supported tournaments",
    description=(
        "Returns the full directory of Sport5 Fantasy tournaments supported by this API, "
        "including each tournament's active status, sport type (Football / Basketball), "
        "current default season ID, and upstream URL name."
    ),
    response_description="A list of all supported Sport5 Fantasy tournaments.",
    tags=["Tournaments"],
)
async def list_tournaments() -> list[TournamentInfo]:
    """Return the static tournament directory with active flags and metadata."""
    return _TOURNAMENT_DIRECTORY


# ---------------------------------------------------------------------------
# Players endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/players",
    response_model=list[Player],
    summary="List all fantasy players",
    description=(
        "Returns the complete fantasy player pool for the requested tournament. "
        "Supports optional filtering by position, min price, max price, and team ID. "
        "Inactive players (injured, suspended, transferred) are excluded by default; "
        "pass ``include_inactive=true`` to include them. "
        "Results are cached for up to 10 minutes."
    ),
    response_description="A list of fantasy players matching the requested filters.",
)
async def list_players(
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
    position: Annotated[
        Position | None,
        Query(
            description=(
                "Filter by player position. Football: GK, DEF, MID, FWD. "
                "Basketball (Euroleague): G, F, C."
            ),
            examples=["MID"],
        ),
    ] = None,
    min_price: Annotated[
        float | None,
        Query(
            ge=0.0,
            description="Include only players whose price is ≥ this value (millions).",
            examples=[5.0],
        ),
    ] = None,
    max_price: Annotated[
        float | None,
        Query(
            ge=0.0,
            description="Include only players whose price is ≤ this value (millions).",
            examples=[7.5],
        ),
    ] = None,
    team_id: Annotated[
        int | None,
        Query(
            ge=1,
            description="Filter by the player's club team ID.",
            examples=[1],
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            description="Case-insensitive substring search by player name.",
            examples=["Zahavi"],
        ),
    ] = None,
    include_inactive: Annotated[
        bool,
        Query(
            description=(
                "When False (default), inactive players are excluded from results. "
                "Set to True to include injured, suspended, or transferred players."
            ),
        ),
    ] = False,
    sort_by: Annotated[
        str | None,
        Query(
            description="Field to sort by: 'price', 'total_points', or 'name'.",
            examples=["price"],
        ),
    ] = None,
    order: Annotated[
        str,
        Query(
            description="Sort direction: 'asc' or 'desc' (default: 'desc').",
            examples=["desc"],
        ),
    ] = "desc",
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=500,
            description="Maximum number of player records to return.",
            examples=[50],
        ),
    ] = None,
    offset: Annotated[
        int,
        Query(
            ge=0,
            description="Zero-based index offset for pagination (default: 0).",
            examples=[0],
        ),
    ] = 0,
) -> list[Player]:
    """Return all players, applying optional filters, search, sorting, and pagination."""
    players = await connector.get_all_players()

    if not include_inactive:
        players = [p for p in players if p.is_active]
    if position is not None:
        players = [p for p in players if p.position == position]
    if min_price is not None:
        players = [p for p in players if p.price >= min_price]
    if max_price is not None:
        players = [p for p in players if p.price <= max_price]
    if team_id is not None:
        players = [p for p in players if p.team_id == team_id]
    if search is not None and search.strip():
        q = search.strip().lower()
        players = [p for p in players if q in p.name.lower()]

    # Sorting
    if sort_by is not None:
        reverse = order.lower() != "asc"
        if sort_by == "price":
            players.sort(key=lambda p: p.price, reverse=reverse)
        elif sort_by == "total_points":
            players.sort(key=lambda p: p.total_points, reverse=reverse)
        elif sort_by == "name":
            players.sort(key=lambda p: p.name.lower(), reverse=reverse)

    # Pagination
    if offset > 0:
        players = players[offset:]
    if limit is not None:
        players = players[:limit]

    logger.debug(
        "[%s] /players returned %d results "
        "(position=%s, min=%s, max=%s, team_id=%s, search=%s, sort_by=%s)",
        connector.tournament_type,
        len(players),
        position,
        min_price,
        max_price,
        team_id,
        search,
        sort_by,
    )
    return players


@router.get(
    "/{tournament}/players/{player_id}",
    response_model=Player,
    summary="Get player by ID",
    description="Returns a single fantasy player by their unique Sport5 player ID.",
    response_description="The matching Player object.",
)
async def get_player(
    player_id: int,
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
) -> Player:
    """Return a single player by ID, or 404 if not found."""
    player = await connector.get_player_by_id(player_id)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Player with ID {player_id} not found in {connector.tournament_type} pool.",
        )
    return player


# ---------------------------------------------------------------------------
# Teams endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/teams",
    response_model=list[Team],
    summary="List all club teams",
    description=(
        "Returns a list of all club teams participating in the current season of "
        "the requested tournament. Each team includes its display name, logo URL, "
        "and shirt/kit URL when available. "
        "Results are cached for up to 1 hour."
    ),
    response_description=("List of club teams with id, name, logo_url, and shirt_url fields."),
)
async def list_teams(
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
) -> list[Team]:
    """Return the full list of club teams with logo and shirt assets."""
    teams = await connector.get_teams()
    logger.debug(
        "[%s] /teams returned %d teams",
        connector.tournament_type,
        len(teams),
    )
    return teams


# ---------------------------------------------------------------------------
# Fixtures endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{tournament}/fixtures",
    response_model=LeagueMetaResponse,
    summary="Get season fixtures and league metadata",
    description=(
        "Returns full season metadata for the requested tournament, including:\n\n"
        "- **current_round**: The active gameweek round index.\n"
        "- **exchange_deadline**: Transfer window lock datetime (when squad changes freeze).\n"
        "- **rounds**: All gameweek rounds with start/end scoring windows.\n"
        "- **games**: All match fixtures with team names, logos, kickoff times, "
        "and completion status.\n\n"
        "Results are cached for up to 15 minutes."
    ),
    response_description=(
        "Full season league metadata including rounds, fixtures, and transfer deadline."
    ),
)
async def get_fixtures(
    connector: Annotated[BaseSport5Connector, Depends(resolve_connector)],
) -> LeagueMetaResponse:
    """Return full league metadata: rounds, all match fixtures, and transfer deadline."""
    result = await connector.get_fixtures()
    logger.debug(
        "[%s] /fixtures returned %d rounds, %d games",
        connector.tournament_type,
        len(result.rounds),
        len(result.games),
    )
    return result

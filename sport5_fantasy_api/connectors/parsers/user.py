"""
Parsers for user private data: squad lineup, joined leagues, and custom leaderboards.
"""

from __future__ import annotations

import logging
from typing import Any

from sport5_fantasy_api.core.exceptions import Sport5DataError
from sport5_fantasy_api.models.enums import PlayerRole
from sport5_fantasy_api.models.league import (
    LeagueLeaderboard,
    LeagueMember,
    LeagueSummary,
)
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

logger = logging.getLogger(__name__)


def parse_user_team_payload(
    data: Any,
    tournament_tag: str = "generic",
) -> UserTeamResponse:
    """
    Parse GetUserAndTeam response into a UserTeamResponse domain model.

    Separates players into starting lineup and bench, and identifies captain/vice-captain.
    """
    top_level: dict[str, Any] = data.get("data", {}) if isinstance(data, dict) else {}
    user_team_raw: dict[str, Any] = top_level.get("userTeam", {})

    if not user_team_raw:
        raise Sport5DataError(
            "GetUserAndTeam response missing 'data.userTeam' field. "
            "The session cookie may be invalid or the user has no team."
        )

    uid: str | int = str(user_team_raw.get("userId", ""))
    user_name: str = str(user_team_raw.get("userName", ""))
    team_name: str = str(user_team_raw.get("teamName", ""))
    budget: float | None = user_team_raw.get("budget")

    players_raw: list[dict[str, Any]] = user_team_raw.get("userTeamPlayers", [])

    starters: list[RosterPlayer] = []
    bench: list[RosterPlayer] = []
    captain: RosterPlayer | None = None
    sub_captain: RosterPlayer | None = None

    for raw in players_raw:
        try:
            roster_player = RosterPlayer.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed roster player record %r: %s",
                tournament_tag,
                raw.get("playerId"),
                exc,
            )
            continue

        if roster_player.is_bench:
            bench.append(roster_player)
        else:
            starters.append(roster_player)

        if roster_player.role == PlayerRole.CAPTAIN:
            captain = roster_player
        elif roster_player.role == PlayerRole.SUB_CAPTAIN:
            sub_captain = roster_player

    return UserTeamResponse(
        user_id=uid,
        user_name=user_name,
        team_name=team_name,
        budget_remaining=float(budget) if budget is not None else None,
        starters=starters,
        bench=bench,
        captain=captain,
        sub_captain=sub_captain,
    )


def parse_user_leagues_payload(
    data: Any,
    tournament_tag: str = "generic",
) -> list[LeagueSummary]:
    """Parse GetLeaguesSummary response into a list of LeagueSummary models."""
    raw_data: Any = data.get("data", []) if isinstance(data, dict) else data
    if isinstance(raw_data, dict):
        leagues_raw: list[dict[str, Any]] = raw_data.get("leagues", [])
    else:
        leagues_raw = list(raw_data) if raw_data else []

    summaries: list[LeagueSummary] = []
    for raw in leagues_raw:
        try:
            summaries.append(LeagueSummary.model_validate(raw))
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed league summary record: %s",
                tournament_tag,
                exc,
            )

    return summaries


def parse_leaderboard_payload(
    data: Any,
    league_id: int,
    page_index: int = 0,
    tournament_tag: str = "generic",
) -> LeagueLeaderboard:
    """Parse GetLeagueData response into a paginated LeagueLeaderboard model."""
    raw_data: Any = data.get("data", {}) if isinstance(data, dict) else data
    if not isinstance(raw_data, dict):
        raw_data = {}

    members_raw = raw_data.get("members") or raw_data.get("userLeagues") or []
    if not isinstance(members_raw, list):
        members_raw = []

    members: list[LeagueMember] = []
    for raw in members_raw:
        try:
            members.append(LeagueMember.model_validate(raw))
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed league member record: %s",
                tournament_tag,
                exc,
            )

    return LeagueLeaderboard(
        league_id=int(raw_data.get("leagueId") or league_id),
        league_name=str(
            raw_data.get("leagueName") or raw_data.get("name") or f"League {league_id}"
        ),
        total_members=int(
            raw_data.get("totalMembers") or raw_data.get("membersCount") or len(members)
        ),
        page_index=int(raw_data.get("pageIndex", page_index)),
        members=members,
    )

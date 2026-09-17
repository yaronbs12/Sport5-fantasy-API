"""
Parsers for Sport5 league fixtures, rounds, and transfer deadlines.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sport5_fantasy_api.models.fixture import (
    LeagueMetaResponse,
    Match,
    RoundInfo,
    _parse_sport5_datetime,
)

logger = logging.getLogger(__name__)


def parse_fixtures_payload(
    league_data: dict[str, Any],
    season_id: int,
    tournament_tag: str = "generic",
) -> LeagueMetaResponse:
    """
    Parse league details JSON into a validated LeagueMetaResponse model.

    Sorts rounds chronologically by round index and matches by kickoff time.
    """
    # --- Rounds (data.allRounds) ---
    rounds: list[RoundInfo] = []
    for raw in league_data.get("allRounds", []):
        try:
            rounds.append(RoundInfo.model_validate(raw))
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed round record: %s",
                tournament_tag,
                exc,
            )
    rounds.sort(key=lambda r: r.round_index)

    # --- Games (data.games) ---
    games: list[Match] = []
    for raw in league_data.get("games", []):
        try:
            games.append(Match.model_validate(raw))
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed game record %r: %s",
                tournament_tag,
                raw.get("id"),
                exc,
            )
    games.sort(key=lambda g: g.kickoff_time)

    # --- Transfer deadline (exchangeEndDate is the lock point) ---
    deadline_raw: Any = league_data.get("exchangeEndDate")
    exchange_deadline: datetime | None = None
    if deadline_raw:
        parsed = _parse_sport5_datetime(deadline_raw)
        if isinstance(parsed, datetime):
            exchange_deadline = parsed
        elif isinstance(parsed, str):
            try:
                exchange_deadline = datetime.fromisoformat(parsed)
            except ValueError:
                logger.warning(
                    "[%s] Could not parse exchangeEndDate: %r",
                    tournament_tag,
                    deadline_raw,
                )

    return LeagueMetaResponse(
        season_id=season_id,
        season_name=str(league_data.get("seasonName", "")),
        current_round=int(league_data.get("currentRound", 0)),
        exchange_deadline=exchange_deadline,
        rounds=rounds,
        games=games,
    )

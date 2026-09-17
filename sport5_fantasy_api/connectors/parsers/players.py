"""
Parsers for Sport5 player payloads.

Handles multiple upstream payload structures (direct list, nested team rosters,
dictionary envelopes) and normalizes player records.
"""

from __future__ import annotations

import logging
from typing import Any

from sport5_fantasy_api.models.player import Player

logger = logging.getLogger(__name__)


def extract_raw_players(raw_payload: Any) -> list[dict[str, Any]]:
    """
    Extract a flattened list of player dictionaries from varied Sport5 upstream shapes.

    Supported shapes:
      - Shape A: {"data": {"players": [...]}} or {"players": [...]}
      - Shape B: {"data": {"teams": [{"players": [...]}]}}
      - Shape C: [{"players": [...]}] or list of player dictionaries
    """
    data = (
        raw_payload.get("data")
        if isinstance(raw_payload, dict) and "data" in raw_payload
        else raw_payload
    )
    raw_players: list[dict[str, Any]] = []

    if isinstance(data, dict):
        # Case A: direct 'players' list
        if "players" in data and isinstance(data["players"], list):
            raw_players = [p for p in data["players"] if isinstance(p, dict)]
        # Case B: 'teams' list containing nested 'players'
        elif "teams" in data and isinstance(data["teams"], list):
            for team in data["teams"]:
                if isinstance(team, dict):
                    t_id = team.get("id") or team.get("teamId")
                    t_name = team.get("name") or team.get("teamName") or "Unknown"
                    team_players = team.get("players") or team.get("teamPlayers") or []
                    if isinstance(team_players, list):
                        for p_dict in team_players:
                            if isinstance(p_dict, dict):
                                merged = dict(p_dict)
                                if (
                                    "teamId" not in merged
                                    and "team_id" not in merged
                                    and t_id is not None
                                ):
                                    merged["teamId"] = t_id
                                if "teamName" not in merged and "team_name" not in merged:
                                    merged["teamName"] = t_name
                                raw_players.append(merged)
    elif isinstance(data, list):
        # Case C: list of team dicts or list of player dicts
        for item in data:
            if isinstance(item, dict):
                if "players" in item or "teamPlayers" in item:
                    t_id = item.get("id") or item.get("teamId")
                    t_name = item.get("name") or item.get("teamName") or "Unknown"
                    team_players = item.get("players") or item.get("teamPlayers") or []
                    if isinstance(team_players, list):
                        for p_dict in team_players:
                            if isinstance(p_dict, dict):
                                merged = dict(p_dict)
                                if (
                                    "teamId" not in merged
                                    and "team_id" not in merged
                                    and t_id is not None
                                ):
                                    merged["teamId"] = t_id
                                if "teamName" not in merged and "team_name" not in merged:
                                    merged["teamName"] = t_name
                                raw_players.append(merged)
                else:
                    raw_players.append(item)

    return raw_players


def parse_players_list(
    raw_players: list[Any],
    tournament_tag: str = "generic",
) -> list[Player]:
    """Validate and construct Player domain models from a list of raw dictionaries."""
    players: list[Player] = []
    for raw in raw_players:
        if not isinstance(raw, dict):
            continue
        try:
            player_raw = dict(raw)
            is_active = (
                (not player_raw.get("isDeleted", False))
                and (not player_raw.get("isRemoved", False))
                and player_raw.get("isActive", True)
            )
            player_raw["isActive"] = is_active
            player_raw["is_active"] = is_active
            players.append(Player.model_validate(player_raw))
        except Exception as exc:
            logger.warning(
                "[%s] Skipping malformed player record %r: %s",
                tournament_tag,
                raw.get("playerId") or raw.get("id"),
                exc,
            )
    return players

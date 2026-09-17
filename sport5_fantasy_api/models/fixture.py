"""
Pydantic V2 models for match fixtures, rounds, and league metadata.

Verified against the live Sport5 ``/api/Leagues/Get`` response structure:

``data`` envelope contains:
  - ``teams``             → list of team objects (id / name / logo paths)
  - ``allRounds``         → list of round objects (id / roundIndex / startDate / endDate)
  - ``games``             → list of match objects (full fixture detail)
  - ``currentRound``      → int  — index of the active round
  - ``seasonName``        → str  — human-readable season label
  - ``exchangeStartDate`` → ISO str | None — start of next transfer window
  - ``exchangeEndDate``   → ISO str | None — end / lock of next transfer window

Game status values observed upstream:
  0 = Scheduled / not started
  1 = Pre-match
  2 = First half
  3 = Half time
  4 = Second half
  5 = Extra time / penalties
  6 = Full time (finished)
  9 = Postponed / cancelled
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Game status code that indicates a completed match.
_GAME_STATUS_FINISHED: int = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sport5_datetime(v: object) -> object:
    """
    Normalise Sport5 date values to Python ``datetime``.

    Handles three upstream formats:
    - Already a ``datetime`` → returned as-is.
    - Epoch **milliseconds** integer/float → converted via timezone-aware ``fromtimestamp``.
    - ISO-8601 string (with or without trailing 'Z' / offset) → returned as-is
      for Pydantic's built-in datetime parsing.
    """
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    if isinstance(v, str):
        # Normalise trailing 'Z' that Python's fromisoformat doesn't accept
        # before 3.11.
        return v.replace("Z", "+00:00")
    return v


# ---------------------------------------------------------------------------
# Team model (used by /teams endpoint)
# ---------------------------------------------------------------------------


class Team(BaseModel):
    """
    A club team participating in the fantasy season.

    Upstream field mapping (``data.teams`` array):
      - ``id``             → ``id``
      - ``name``           → ``name``
      - ``teamLogoPath``   → ``logo_url``
      - ``teamShirtPath``  → ``shirt_url``
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., alias="id", description="Unique Sport5 team identifier.")
    name: str = Field(..., alias="name", description="Club display name.")
    logo_url: str | None = Field(
        default=None,
        alias="teamLogoPath",
        description="CDN path to the club crest / logo image.",
    )
    shirt_url: str | None = Field(
        default=None,
        alias="teamShirtPath",
        description="CDN path to the club shirt / kit image.",
    )


# ---------------------------------------------------------------------------
# Round model
# ---------------------------------------------------------------------------


class RoundInfo(BaseModel):
    """
    A single gameweek (round) within the fantasy season.

    Upstream field mapping (``data.allRounds`` array):
      - ``id``          → ``id``
      - ``roundIndex``  → ``round_index``
      - ``startDate``   → ``start_date``
      - ``endDate``     → ``end_date``
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., alias="id", description="Unique Sport5 round identifier.")
    round_index: int = Field(
        ...,
        alias="roundIndex",
        ge=1,
        description="1-based sequential gameweek index within the season.",
    )
    start_date: datetime = Field(
        ...,
        alias="startDate",
        description="Datetime when this round's scoring window opens.",
    )
    end_date: datetime = Field(
        ...,
        alias="endDate",
        description="Datetime when this round's scoring window closes.",
    )

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalise_date(cls, v: object) -> object:
        """Accept epoch-ms integers, ISO strings, or existing datetimes."""
        return _parse_sport5_datetime(v)


# ---------------------------------------------------------------------------
# Match model
# ---------------------------------------------------------------------------


class Match(BaseModel):
    """
    A single fixture (match) within a round.

    Upstream field mapping (``data.games`` array):
      - ``id``           → ``id``
      - ``roundId``      → ``round_id``
      - ``teamAId``      → ``home_team_id``
      - ``teamAName``    → ``home_team_name``
      - ``teamALogo``    → ``home_team_logo``
      - ``teamBId``      → ``away_team_id``
      - ``teamBName``    → ``away_team_name``
      - ``teamBLogo``    → ``away_team_logo``
      - ``gameStart``    → ``kickoff_time``
      - ``gameStatus``   → ``game_status`` (raw int) + derived ``is_finished``
      - ``resultData``   → ``result_data`` (optional parsed JSON string)
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., alias="id", description="Unique Sport5 match identifier.")
    round_id: int = Field(
        ...,
        alias="roundId",
        description="ID of the round this match belongs to.",
    )

    # Home team (teamA in Sport5 payload)
    home_team_id: int = Field(..., alias="teamAId")
    home_team_name: str = Field(..., alias="teamAName")
    home_team_logo: str | None = Field(default=None, alias="teamALogo")

    # Away team (teamB in Sport5 payload)
    away_team_id: int = Field(..., alias="teamBId")
    away_team_name: str = Field(..., alias="teamBName")
    away_team_logo: str | None = Field(default=None, alias="teamBLogo")

    # Timing
    kickoff_time: datetime = Field(
        ...,
        alias="gameStart",
        description="Scheduled or actual kickoff datetime.",
    )

    # Status
    game_status: int = Field(
        default=0,
        alias="gameStatus",
        description=(
            "Raw Sport5 game status code. "
            "0=Scheduled, 2=1st Half, 3=HT, 4=2nd Half, 6=FT, 9=Postponed."
        ),
    )
    is_finished: bool = Field(
        default=False,
        description="True when the match result has been finalised (gameStatus == 6).",
    )

    # Optional result payload
    result_data: dict[str, Any] | None = Field(
        default=None,
        alias="resultData",
        description="Parsed match result and event data (goals, cards, etc.).",
    )

    @field_validator("kickoff_time", mode="before")
    @classmethod
    def _normalise_kickoff(cls, v: object) -> object:
        """Normalise epoch-ms or ISO string to datetime."""
        return _parse_sport5_datetime(v)

    @model_validator(mode="before")
    @classmethod
    def _derive_is_finished(cls, data: object) -> object:
        """
        Derive ``is_finished`` from ``gameStatus`` and ``resultData``.

        A status of 6 (full time) marks the match as finished.
        Also considers populated ``resultData`` when game is not live/in-progress.
        """
        if not isinstance(data, dict):
            return data
        status: int = int(data.get("gameStatus", 0))
        result_raw: Any = data.get("resultData")
        has_result = False
        if isinstance(result_raw, dict):
            has_result = bool(result_raw)
        elif isinstance(result_raw, str):
            cleaned = result_raw.strip()
            has_result = bool(cleaned and cleaned not in ("null", "{}", "[]"))

        # Live in-progress statuses (e.g. 2=1st half, 3=HT, 4=2nd half) or postponed (9)
        # are not finished even if live resultData is attached.
        data["is_finished"] = (status == _GAME_STATUS_FINISHED) or (
            status not in (1, 2, 3, 4, 9) and has_result
        )
        return data

    @field_validator("result_data", mode="before")
    @classmethod
    def _parse_result_data(cls, v: object) -> object:
        """
        Sport5 sends ``resultData`` as a **JSON string** inside the outer JSON.
        Parse it to a dict if it's a non-empty string; leave it as-is otherwise.
        """
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                logger.warning("Could not parse resultData JSON: %r", v[:120])
                return None
        return v if isinstance(v, dict) else None


# ---------------------------------------------------------------------------
# Top-level league metadata response
# ---------------------------------------------------------------------------


class LeagueMetaResponse(BaseModel):
    """
    Full league metadata returned by the ``/fixtures`` API endpoint.

    Aggregates the key scheduling and configuration data from
    ``GET /api/Leagues/Get?seasonId={id}``.

    Fields
    ------
    season_id:
        The numeric Sport5 season identifier.
    season_name:
        Human-readable season label (e.g. ``"2024/25"``).
    current_round:
        The ``roundIndex`` of the currently active gameweek.
    exchange_deadline:
        The **end** of the next transfer window (the lock datetime).
        ``None`` if not yet announced upstream.
    rounds:
        All gameweek rounds for this season, ordered by ``round_index``.
    games:
        All match fixtures for this season, ordered by ``kickoff_time``.
    """

    model_config = ConfigDict(populate_by_name=True)

    season_id: int = Field(..., alias="seasonId", description="Sport5 numeric season identifier.")
    season_name: str = Field(
        default="",
        alias="seasonName",
        description="Human-readable season label (e.g. '2024/25').",
    )
    current_round: int = Field(
        default=0,
        alias="currentRound",
        description="Round index of the currently active gameweek (0 if not set).",
    )
    exchange_deadline: datetime | None = Field(
        default=None,
        alias="exchangeDeadline",
        description=(
            "Transfer window lock datetime — the point after which squad changes "
            "are frozen for the upcoming round. None if not yet announced."
        ),
    )
    rounds: list[RoundInfo] = Field(
        default_factory=list,
        description="All gameweek rounds, ordered ascending by round_index.",
    )
    games: list[Match] = Field(
        default_factory=list,
        description="All match fixtures, ordered ascending by kickoff_time.",
    )

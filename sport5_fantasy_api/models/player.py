"""
Pydantic V2 models for football players and per-round player statistics.

Key design decisions:
  - `Player.name` is sanitised via a field_validator to replace backtick
    characters (`` ` ``) sometimes present in Sport5's Hebrew player names
    with standard typographic apostrophes (``'``).
  - All upstream field names use camelCase; we map them to snake_case via
    `Field(alias=...)` and enable `model_config` population by both name
    and alias.
  - `price` is kept as a plain ``float``; Sport5 expresses it in millions
    (e.g. ``5.5`` == 5.5M).
"""

from __future__ import annotations

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from sport5_fantasy_api.models.enums import Position


class Player(BaseModel):
    """
    Represents a single player in the Sport5 fantasy player pool.

    Upstream field mapping (Sport5 → our model):
      - ``playerId`` / ``id``       → ``id``
      - ``playerName`` / ``name``   → ``name``
      - ``teamId`` / ``team_id``    → ``team_id``
      - ``teamName`` / ``team_name``→ ``team_name``
      - ``positionId`` / ``position`` → ``position``  (normalised to ``Position`` enum)
      - ``price``                   → ``price``
      - ``totalPoints`` / ``points`` → ``total_points``
      - ``isActive`` / ``is_active`` → ``is_active``
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        frozen=False,
    )

    id: int = Field(
        ...,
        validation_alias=AliasChoices("playerId", "id", "player_id"),
        description="Unique Sport5 player identifier.",
    )
    name: str = Field(
        ...,
        validation_alias=AliasChoices("playerName", "name", "player_name"),
        description="Player display name.",
    )
    team_id: int = Field(
        ...,
        validation_alias=AliasChoices("teamId", "team_id"),
        description="Owning club team identifier.",
    )
    team_name: str = Field(
        ...,
        validation_alias=AliasChoices("teamName", "team_name"),
        description="Owning club team display name.",
    )
    position: Position = Field(
        ...,
        validation_alias=AliasChoices("positionId", "position", "position_id"),
        description="Normalised player position (GK/DEF/MID/FWD).",
    )
    price: float = Field(
        ...,
        validation_alias=AliasChoices("price", "cost"),
        ge=0.0,
        description="Player price in millions of fantasy currency units.",
    )
    total_points: int = Field(
        default=0,
        validation_alias=AliasChoices("totalPoints", "points", "total_points"),
        description="Accumulated fantasy points for the current season.",
    )
    is_active: bool = Field(
        default=True,
        validation_alias=AliasChoices("isActive", "is_active", "active"),
        description=(
            "Whether the player is currently active / available for selection. "
            "Inactive players may be injured, suspended, or transferred."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _resolve_active_flags(cls, data: object) -> object:
        """
        Normalize active status from raw upstream flags if present.
        Inverts isDeleted/isRemoved (True -> inactive).
        """
        if isinstance(data, dict) and ("isDeleted" in data or "isRemoved" in data):
            is_active = (
                (not data.get("isDeleted", False))
                and (not data.get("isRemoved", False))
                and data.get("isActive", data.get("is_active", data.get("active", True)))
            )
            data["isActive"] = is_active
            data["is_active"] = is_active
        return data

    @field_validator("name", mode="after")
    @classmethod
    def _sanitise_name(cls, v: str) -> str:
        """Replace backtick characters with standard single-quote apostrophes."""
        return v.replace("`", "'")

    @field_validator("position", mode="before")
    @classmethod
    def _normalise_position(cls, v: object) -> object:
        """
        Normalise position identifier to a ``Position`` enum value.

        Handles:
          - Football numeric IDs: 1 -> GK, 2 -> DEF, 3 -> MID, 4 -> FWD
          - Basketball numeric IDs: 5 -> C
          - Direct string representations (e.g. 'G', 'F', 'C', 'GUARD', 'FORWARD', 'CENTER')
          - Fallback to Position.UNKNOWN instead of dropping the player record
        """
        _numeric_map: dict[int, str] = {
            1: "GK",
            2: "DEF",
            3: "MID",
            4: "FWD",
            5: "C",
        }
        _string_map: dict[str, str] = {
            "GK": "GK",
            "GOALKEEPER": "GK",
            "DEF": "DEF",
            "DEFENDER": "DEF",
            "MID": "MID",
            "MIDFIELDER": "MID",
            "FWD": "FWD",
            "FORWARD": "FWD",
            "G": "G",
            "GUARD": "G",
            "F": "F",
            "C": "C",
            "CENTER": "C",
        }

        if isinstance(v, (int, str)):
            if isinstance(v, str) and v.isdigit():
                v = int(v)
            if isinstance(v, int):
                return _numeric_map.get(v, Position.UNKNOWN)
            if isinstance(v, str):
                normalized = _string_map.get(v.strip().upper())
                if normalized is not None:
                    return normalized
                try:
                    return Position(v)
                except ValueError:
                    return Position.UNKNOWN
        return v

    @field_validator("price", mode="before")
    @classmethod
    def _normalise_price(cls, v: object) -> object:
        """
        Sport5 sometimes sends price as a raw integer in sub-units
        (e.g. ``5_000_000`` → 5.0M).  If the value is ≥ 100_000 we
        divide by 1_000_000 to bring it into the expected millions range.
        """
        if isinstance(v, (int, float)) and v >= 100_000:
            return float(v) / 1_000_000.0
        return v


class RoundStat(BaseModel):
    """
    Per-round statistics for a player (points earned in a specific gameweek).

    Supports both football statistics (goals, assists, cards, clean sheets)
    and basketball statistics (rebounds, blocks, steals, turnovers, PIR).
    """

    model_config = ConfigDict(populate_by_name=True)

    player_id: int = Field(..., alias="playerId")
    round_number: int = Field(..., alias="roundNum")
    round_points: int = Field(default=0, alias="roundPoints")
    minutes_played: int | None = Field(default=None, alias="minutesPlayed")

    # Football-specific metrics
    goals: int = Field(default=0, alias="goals")
    assists: int = Field(default=0, alias="assists")
    yellow_cards: int = Field(default=0, alias="yellowCards")
    red_cards: int = Field(default=0, alias="redCards")
    clean_sheet: bool = Field(default=False, alias="cleanSheet")

    # Basketball-specific metrics (Euroleague)
    rebounds: int = Field(default=0, alias="rebounds")
    blocks: int = Field(default=0, alias="blocks")
    steals: int = Field(default=0, alias="steals")
    turnovers: int = Field(default=0, alias="turnovers")
    pir: float | None = Field(default=None, alias="pir", description="Performance Index Rating")

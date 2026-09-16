"""
Pydantic V2 models for user squad and team data.

The Sport5 ``/api/UserTeam/GetUserAndTeam`` endpoint returns a nested JSON
structure.  Key fields inside ``data.userTeam``:

  - ``userId``              → authenticated user ID
  - ``userName``            → display name
  - ``teamName``            → fantasy team name
  - ``budget``              → remaining transfer budget (millions)
  - ``userTeamPlayers``     → list of player objects, each containing:
      - ``playerId``        → int
      - ``playerName``      → str
      - ``teamId``          → int
      - ``teamName``        → str
      - ``positionId``      → int | str  (normalised by Player validator)
      - ``price``           → float
      - ``totalPoints``     → int
      - ``isActive``        → bool
      - ``isReserve``       → bool  (True = bench, False = starter)
      - ``isCaptain``       → bool
      - ``isSubCaptain``    → bool

We split the flat list into ``starters`` and ``bench`` lists and promote the
captain / sub-captain into dedicated nullable fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from sport5_fantasy_api.models.enums import PlayerRole, Position


class RosterPlayer(BaseModel):
    """
    A player as they appear within a user's squad roster.

    Extends the base player attributes with:
      - ``is_bench``  — whether the player is a substitute (bench) or a starter.
      - ``role``      — CAPTAIN, SUB_CAPTAIN, or plain PLAYER.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., alias="playerId")
    name: str = Field(..., alias="playerName")
    team_id: int = Field(..., alias="teamId")
    team_name: str = Field(..., alias="teamName")
    position: Position = Field(..., alias="positionId")
    price: float = Field(..., alias="price", ge=0.0)
    total_points: int = Field(default=0, alias="totalPoints")
    is_active: bool = Field(default=True, alias="isActive")
    is_bench: bool = Field(
        default=False,
        alias="isReserve",
        description="True when the player is on the substitutes' bench.",
    )
    role: PlayerRole = Field(
        default=PlayerRole.PLAYER,
        description="Captain / sub-captain / regular player designation.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _derive_role(cls, data: object) -> object:
        """
        Derive the ``role`` field from the ``isCaptain`` and ``isSubCaptain``
        boolean flags before the standard field validation runs.

        We consume the upstream booleans and replace ``role`` with the
        appropriate ``PlayerRole`` value.
        """
        if not isinstance(data, dict):
            return data
        is_captain: bool = bool(data.get("isCaptain", False))
        is_sub_captain: bool = bool(data.get("isSubCaptain", False))
        if is_captain:
            data["role"] = PlayerRole.CAPTAIN
        elif is_sub_captain:
            data["role"] = PlayerRole.SUB_CAPTAIN
        else:
            data["role"] = PlayerRole.PLAYER
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalise_position(cls, data: object) -> object:
        """
        Map numeric positionId integers or strings so the ``Position`` enum can parse them.
        """
        if not isinstance(data, dict):
            return data
        _numeric_map: dict[int, str] = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD", 5: "C"}
        _string_map: dict[str, str] = {
            "GK": "GK",
            "DEF": "DEF",
            "MID": "MID",
            "FWD": "FWD",
            "G": "G",
            "F": "F",
            "C": "C",
        }
        position_raw = data.get("positionId")
        if isinstance(position_raw, str) and position_raw.isdigit():
            position_raw = int(position_raw)
        if isinstance(position_raw, int):
            data["positionId"] = _numeric_map.get(position_raw, Position.UNKNOWN.value)
        elif isinstance(position_raw, str):
            norm = _string_map.get(position_raw.strip().upper())
            if norm:
                data["positionId"] = norm
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_captain(self) -> bool:
        """Convenience property: True when role == CAPTAIN."""
        return self.role == PlayerRole.CAPTAIN

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sub_captain(self) -> bool:
        """Convenience property: True when role == SUB_CAPTAIN."""
        return self.role == PlayerRole.SUB_CAPTAIN


class UserTeamResponse(BaseModel):
    """
    The complete squad response for a single authenticated user.

    Fields
    ------
    user_id:
        Sport5 user identifier (may be returned as an int or string).
    user_name:
        The user's Sport5 display name.
    team_name:
        The user's chosen fantasy team name.
    budget_remaining:
        Remaining transfer budget in millions of fantasy currency.
        ``None`` if the upstream omits this value.
    starters:
        Ordered list of starting XI / selected players (``is_bench=False``).
    bench:
        Ordered list of substitute / bench players (``is_bench=True``).
    captain:
        The designated captain, or ``None`` if not yet set.
    sub_captain:
        The designated vice-captain, or ``None`` if not yet set.
    """

    model_config = ConfigDict(populate_by_name=True)

    user_id: str | int = Field(
        ...,
        description="Sport5 user identifier.",
    )
    user_name: str = Field(..., description="Sport5 display name.")
    team_name: str = Field(..., description="Fantasy team name chosen by the user.")
    budget_remaining: float | None = Field(
        default=None,
        ge=0.0,
        description="Remaining transfer budget (millions). Null if unavailable.",
    )
    starters: list[RosterPlayer] = Field(
        default_factory=list,
        description="Starting players (first eleven / active lineup).",
    )
    bench: list[RosterPlayer] = Field(
        default_factory=list,
        description="Bench / substitute players.",
    )
    captain: RosterPlayer | None = Field(
        default=None,
        description="The designated fantasy captain (earns double points).",
    )
    sub_captain: RosterPlayer | None = Field(
        default=None,
        description=(
            "The designated vice-captain (earns double points if captain "
            "does not play)."
        ),
    )

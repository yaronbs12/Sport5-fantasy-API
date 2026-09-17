"""
Pydantic V2 models for fantasy leagues and leaderboard data.

Sport5 league data arrives from two distinct endpoints:
  1. ``/api/CustomLeagues/GetLeaguesSummary``  → list of ``LeagueSummary``
  2. ``/api/CustomLeagues/GetLeagueData``       → paginated ``LeagueMember`` rows

Both models use ``populate_by_name=True`` so callers can construct instances
with either snake_case Python names or the original camelCase upstream aliases.
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LeagueSummary(BaseModel):
    """
    A brief summary of a custom league that the authenticated user belongs to.

    Upstream field mapping (Sport5 → our model):
      - ``leagueId`` / ``id``          → ``id``
      - ``leagueName`` / ``name``      → ``name``
      - ``membersCount`` / ``usersCount`` → ``member_count``
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(
        ...,
        validation_alias=AliasChoices("leagueId", "id", "league_id"),
        description="Unique league identifier.",
    )
    name: str = Field(
        ...,
        validation_alias=AliasChoices("leagueName", "name", "league_name"),
        description="Display name of the league.",
    )
    member_count: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "membersCount", "usersCount", "member_count", "members_count", "userCount"
        ),
        ge=0,
        description="Total number of teams enrolled in this league.",
    )


class LeagueMember(BaseModel):
    """
    A single row in a league leaderboard.

    Upstream field mapping (Sport5 → our model):
      - ``userId``       → ``user_id``
      - ``teamName``     → ``team_name``
      - ``userName``     → ``user_name``
      - ``totalPoints``  → ``total_points``
      - ``rank``         → ``rank``
    """

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        ...,
        alias="userId",
        description="Unique Sport5 user identifier (may be numeric string).",
    )
    user_name: str = Field(..., alias="userName", description="Sport5 username / display name.")
    team_name: str = Field(
        ..., alias="teamName", description="The user's fantasy team display name."
    )
    total_points: int | None = Field(
        default=None,
        alias="totalPoints",
        description="Cumulative fantasy points earned in this league.",
    )
    rank: int | None = Field(
        default=None,
        alias="rank",
        ge=1,
        description="Current position on the league leaderboard (1-indexed).",
    )


class LeagueLeaderboard(BaseModel):
    """
    Full paginated leaderboard response for a single league.
    """

    model_config = ConfigDict(populate_by_name=True)

    league_id: int = Field(..., alias="leagueId")
    league_name: str = Field(..., alias="leagueName")
    total_members: int = Field(default=0, alias="totalMembers")
    page_index: int = Field(default=0, alias="pageIndex")
    members: list[LeagueMember] = Field(
        default_factory=list,
        alias="members",
        description="Ordered list of league members for this page.",
    )

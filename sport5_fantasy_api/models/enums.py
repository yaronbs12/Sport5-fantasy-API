"""
Domain enumerations for the Sport5 Fantasy API.

Using `StrEnum` (Python 3.11+) so enum members are directly comparable to
plain strings — useful for Pydantic V2 discriminators and JSON serialisation.
"""

from __future__ import annotations

import sys
from enum import Enum

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python 3.10."""

        def __str__(self) -> str:
            return str(self.value)


class TournamentType(StrEnum):
    """Supported Sport5 fantasy tournament competitions.

    Upstream ``urlName`` mapping (from ``/api/Leagues/GetLeagues``):
      - ``"dreamteam"``        → ISRAELI_LEAGUE   (league_id=4,  seasonId=10, sport=Football)
      - ``"fantasyleague"``    → CHAMPIONS_LEAGUE (league_id=5,  seasonId=12, sport=Football)
      - ``"fantasyeuroleague"``→ EUROLEAGUE        (league_id=13, seasonId=11, sport=Basketball)
      - ``"fantasywc"``        → WORLD_CUP         (league_id=14, seasonId=9,  sport=Football)
      - ``"eurofantasy"``      → EURO              (league_id=6,  seasonId=3,  active=False)
    """

    ISRAELI_LEAGUE = "israel"
    CHAMPIONS_LEAGUE = "champions"
    EUROLEAGUE = "euroleague"
    EURO = "euro"
    WORLD_CUP = "world-cup"


class Position(StrEnum):
    """Player positions across football and basketball competitions."""

    # Football positions
    GK = "GK"  # Goalkeeper
    DEF = "DEF"  # Defender
    MID = "MID"  # Midfielder
    FWD = "FWD"  # Forward

    # Basketball positions (Euroleague)
    GUARD = "G"  # Guard
    FORWARD = "F"  # Forward
    CENTER = "C"  # Center

    # Fallback for unexpected upstream values
    UNKNOWN = "UNKNOWN"


class PlayerRole(StrEnum):
    """
    The role a player occupies within a fantasy squad lineup.

    Note: A player can simultaneously be a CAPTAIN and a starter, or a
    SUB_CAPTAIN and a starter; the `is_bench` flag on `RosterPlayer`
    carries the starter/bench distinction independently.
    """

    PLAYER = "player"
    CAPTAIN = "captain"
    SUB_CAPTAIN = "sub_captain"

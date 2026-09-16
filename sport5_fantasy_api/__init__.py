"""
sport5-fantasy — Public package surface.

Import the most commonly used symbols here so library consumers can write::

    from sport5_fantasy_api import TournamentType, Player
    from sport5_fantasy_api import IsraeliLeagueConnector
"""

from __future__ import annotations

from sport5_fantasy_api.connectors.champions_league import ChampionsLeagueConnector
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.connectors.registry import ConnectorRegistry, registry
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import (
    ConnectorNotFoundError,
    Sport5AuthError,
    Sport5DataError,
    Sport5FantasyAPIError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)
from sport5_fantasy_api.models.auth import LoginRequest, TokenResponse
from sport5_fantasy_api.models.enums import PlayerRole, Position, TournamentType
from sport5_fantasy_api.models.fixture import LeagueMetaResponse, Match, RoundInfo, Team
from sport5_fantasy_api.models.league import LeagueLeaderboard, LeagueMember, LeagueSummary
from sport5_fantasy_api.models.player import Player, RoundStat
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

__version__: str = "0.1.0"
__author__: str = "sport5-fantasy contributors"
__license__: str = "MIT"

__all__ = [
    "ChampionsLeagueConnector",
    "ConnectorNotFoundError",
    "ConnectorRegistry",
    # Connectors
    "IsraeliLeagueConnector",
    "LeagueLeaderboard",
    "LeagueMember",
    "LeagueMetaResponse",
    # League models
    "LeagueSummary",
    "LoginRequest",
    "Match",
    # Player models
    "Player",
    "PlayerRole",
    "Position",
    # User models
    "RosterPlayer",
    "RoundInfo",
    "RoundStat",
    "Sport5AuthError",
    "Sport5DataError",
    # Exceptions
    "Sport5FantasyAPIError",
    "Sport5UpstreamError",
    "Sport5WAFBlockError",
    # Core
    "TTLCache",
    # Fixture / schedule models
    "Team",
    "TokenResponse",
    # Enums
    "TournamentType",
    "UserTeamResponse",
    "__author__",
    "__license__",
    # Version
    "__version__",
    "registry",
    "settings",
]

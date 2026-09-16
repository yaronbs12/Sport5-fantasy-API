"""
Euroleague Basketball fantasy connector.

Targets the Sport5 shared backend at ``https://dreamteam.sport5.co.il``.
The default season ID is ``11``; ``discover_season_id()`` will update
this dynamically from ``/api/Leagues/GetLeagues`` (urlName="fantasyeuroleague").
"""

from __future__ import annotations

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.models.enums import TournamentType


class EuroleagueConnector(BaseSport5Connector):
    """
    Connector for the Euroleague Basketball fantasy platform.

    Upstream base URL:  ``https://dreamteam.sport5.co.il``
    Default season ID:  ``11``
    Sport:             Basketball
    """

    @property
    def base_url(self) -> str:
        return settings.euroleague_base_url

    @property
    def default_season_id(self) -> int:
        return settings.euroleague_default_season_id

    @property
    def tournament_type(self) -> TournamentType:
        return TournamentType.EUROLEAGUE

    def __init__(self, cache: TTLCache | None = None) -> None:
        super().__init__(cache=cache)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"EuroleagueConnector("
            f"base_url={self.base_url!r}, "
            f"default_season_id={self.default_season_id!r})"
        )

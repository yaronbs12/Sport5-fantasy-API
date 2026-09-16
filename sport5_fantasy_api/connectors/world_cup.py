"""
World Cup fantasy connector.

Targets the Sport5 shared backend at ``https://dreamteam.sport5.co.il``.
The default season ID is ``9``; ``discover_season_id()`` will update
this dynamically from ``/api/Leagues/GetLeagues`` (urlName="fantasywc").
"""

from __future__ import annotations

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.models.enums import TournamentType


class WorldCupConnector(BaseSport5Connector):
    """
    Connector for the World Cup fantasy platform.

    Upstream base URL:  ``https://dreamteam.sport5.co.il``
    Default season ID:  ``9``
    Sport:             Football
    """

    @property
    def base_url(self) -> str:
        return settings.world_cup_base_url

    @property
    def default_season_id(self) -> int:
        return settings.world_cup_default_season_id

    @property
    def tournament_type(self) -> TournamentType:
        return TournamentType.WORLD_CUP

    def __init__(self, cache: TTLCache | None = None) -> None:
        super().__init__(cache=cache)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"WorldCupConnector("
            f"base_url={self.base_url!r}, "
            f"default_season_id={self.default_season_id!r})"
        )

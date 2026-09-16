"""
Israeli Premier League ("ליגת החלומות") connector.

Targets the Sport5 Dream Team platform at ``https://dreamteam.sport5.co.il``.
The default season ID for the Israeli Premier League is ``10``; the
``discover_season_id()`` method inherited from ``BaseSport5Connector`` will
update this dynamically from the ``/api/Leagues/GetLeagues`` endpoint.
"""

from __future__ import annotations

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.models.enums import TournamentType


class IsraeliLeagueConnector(BaseSport5Connector):
    """
    Connector for the Israeli Premier League fantasy platform.

    Upstream base URL:  ``https://dreamteam.sport5.co.il``
    Default season ID:  ``10``
    """

    @property
    def base_url(self) -> str:
        return settings.israeli_league_base_url

    @property
    def default_season_id(self) -> int:
        return settings.israeli_league_default_season_id

    @property
    def tournament_type(self) -> TournamentType:
        return TournamentType.ISRAELI_LEAGUE

    def __init__(self, cache: TTLCache | None = None) -> None:
        super().__init__(cache=cache)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"IsraeliLeagueConnector("
            f"base_url={self.base_url!r}, "
            f"default_season_id={self.default_season_id!r})"
        )

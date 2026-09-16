"""Connectors subpackage: Sport5 upstream API connector implementations."""

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.connectors.champions_league import ChampionsLeagueConnector
from sport5_fantasy_api.connectors.euro import EuroConnector
from sport5_fantasy_api.connectors.euroleague import EuroleagueConnector
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.connectors.registry import ConnectorRegistry, registry
from sport5_fantasy_api.connectors.world_cup import WorldCupConnector

__all__ = [
    "BaseSport5Connector",
    "ChampionsLeagueConnector",
    "ConnectorRegistry",
    "EuroConnector",
    "EuroleagueConnector",
    "IsraeliLeagueConnector",
    "WorldCupConnector",
    "registry",
]

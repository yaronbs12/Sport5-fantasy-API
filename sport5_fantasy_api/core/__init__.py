"""Core utilities: configuration, caching, and domain exceptions."""

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

__all__ = [
    "ConnectorNotFoundError",
    "Sport5AuthError",
    "Sport5DataError",
    "Sport5FantasyAPIError",
    "Sport5UpstreamError",
    "Sport5WAFBlockError",
    "TTLCache",
    "settings",
]

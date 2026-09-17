"""
Connector Registry — Factory / Strategy pattern for tournament connectors.

The ``ConnectorRegistry`` maintains a mapping of ``TournamentType`` →
singleton ``BaseSport5Connector`` instance.  Connectors are constructed
lazily on first access to avoid creating HTTP clients at import time.

Thread-safety
-------------
A ``threading.Lock`` guards the internal ``_instances`` dictionary so that
multiple concurrent FastAPI startup coroutines cannot create duplicate
connector instances.  The lock is only held during the brief period of
object construction; subsequent reads are lock-free lookups into an
already-populated dict.

Usage
-----
Typical usage via the module-level singleton::

    from sport5_fantasy_api.connectors.registry import registry
    connector = registry.get_connector(TournamentType.ISRAELI_LEAGUE)
"""

from __future__ import annotations

import threading

from sport5_fantasy_api.connectors.base import BaseSport5Connector
from sport5_fantasy_api.connectors.champions_league import ChampionsLeagueConnector
from sport5_fantasy_api.connectors.euro import EuroConnector
from sport5_fantasy_api.connectors.euroleague import EuroleagueConnector
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.connectors.world_cup import WorldCupConnector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.exceptions import ConnectorNotFoundError
from sport5_fantasy_api.models.enums import TournamentType

# ---------------------------------------------------------------------------
# Type alias for the connector factory map
# ---------------------------------------------------------------------------

_ConnectorClass = type[BaseSport5Connector]

_CONNECTOR_FACTORY_MAP: dict[TournamentType, _ConnectorClass] = {
    TournamentType.ISRAELI_LEAGUE: IsraeliLeagueConnector,
    TournamentType.CHAMPIONS_LEAGUE: ChampionsLeagueConnector,
    TournamentType.EUROLEAGUE: EuroleagueConnector,
    TournamentType.WORLD_CUP: WorldCupConnector,
    TournamentType.EURO: EuroConnector,
}


class ConnectorRegistry:
    """
    Thread-safe singleton registry for ``BaseSport5Connector`` instances.

    One shared ``TTLCache`` is created for all connectors so that data
    cached by one connector (e.g. a shared season ID) can be reused by
    another if they target the same upstream.

    Attributes
    ----------
    _shared_cache:
        A single ``TTLCache`` instance shared across all connectors.
    _instances:
        Mapping of ``TournamentType`` → already-constructed connector.
    _lock:
        ``threading.Lock`` protecting ``_instances`` during construction.
    """

    def __init__(self) -> None:
        self._shared_cache: TTLCache = TTLCache()
        self._instances: dict[TournamentType, BaseSport5Connector] = {}
        self._lock: threading.Lock = threading.Lock()

    def get_connector(self, tournament: TournamentType) -> BaseSport5Connector:
        """
        Return the singleton connector for the given tournament type.

        Parameters
        ----------
        tournament:
            The ``TournamentType`` to resolve.

        Returns
        -------
        BaseSport5Connector
            The connector instance for the requested tournament.

        Raises
        ------
        ConnectorNotFoundError
            If no connector is registered for ``tournament``.
        """
        # Fast path — lock-free read after initial construction.
        if tournament in self._instances:
            return self._instances[tournament]

        factory = _CONNECTOR_FACTORY_MAP.get(tournament)
        if factory is None:
            raise ConnectorNotFoundError(str(tournament))

        with self._lock:
            # Double-checked locking: another thread may have created it
            # while we were waiting for the lock.
            if tournament not in self._instances:
                self._instances[tournament] = factory(cache=self._shared_cache)

        return self._instances[tournament]

    def register(
        self,
        tournament: TournamentType,
        connector_class: _ConnectorClass,
        *,
        replace: bool = False,
    ) -> None:
        """
        Register a new connector class for a given tournament type.

        Parameters
        ----------
        tournament:
            The tournament type to associate with ``connector_class``.
        connector_class:
            The concrete ``BaseSport5Connector`` subclass to instantiate.
        replace:
            If ``False`` (default) and an instance already exists for
            ``tournament``, raises ``ValueError``.  Set to ``True`` to
            forcefully replace an existing mapping (use with care in tests).

        Raises
        ------
        ValueError
            If ``tournament`` is already registered and ``replace=False``.
        """
        with self._lock:
            if tournament in self._instances and not replace:
                raise ValueError(
                    f"A connector for tournament {tournament!r} is already registered. "
                    "Pass replace=True to override."
                )
            # Update factory map and reset the instance so it is re-created.
            _CONNECTOR_FACTORY_MAP[tournament] = connector_class
            self._instances.pop(tournament, None)

    async def close_all(self) -> None:
        """Close all instantiated connector HTTP clients gracefully."""
        with self._lock:
            instances = list(self._instances.values())
        for connector in instances:
            await connector.aclose()

    def __repr__(self) -> str:  # pragma: no cover
        return f"ConnectorRegistry(registered={list(self._instances.keys())!r})"


# ---------------------------------------------------------------------------
# Module-level singleton — import this in dependencies.py and elsewhere.
# ---------------------------------------------------------------------------

registry: ConnectorRegistry = ConnectorRegistry()

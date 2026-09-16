"""
Asynchronous in-memory TTL (Time-To-Live) cache.

Design goals:
  - Fully async-safe: all mutations are guarded by an `asyncio.Lock`.
  - Lazy expiry: expired keys are pruned on access rather than via a background
    sweep task, keeping the implementation simple and dependency-free.
  - Generic values: stores `Any` typed values so the cache can serve diverse
    upstream payloads (player lists, team mappings, season IDs, etc.).

Usage example::

    cache = TTLCache()
    await cache.set("players", player_list, ttl_seconds=600)
    players = await cache.get("players")
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


class TTLCache:
    """
    Coroutine concurrency-safe in-memory cache with per-key TTL expiry and bounded capacity.

    All mutations and lookups are guarded by an ``asyncio.Lock`` within the running event loop.
    Enforces a ``max_size`` limit to prevent unbounded memory growth; when capacity is reached,
    expired entries are pruned, and if still full, oldest entries are evicted (FIFO).

    Attributes
    ----------
    _store:
        Internal dictionary mapping ``key -> (value, expiry_timestamp)``.
    _lock:
        ``asyncio.Lock`` guarding all access and mutations to ``_store``.
    _max_size:
        Maximum number of entries permitted in the cache before eviction occurs.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._max_size: int = max_size
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock tied to the current running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock is None or (current_loop is not None and self._loop != current_loop):
            self._lock = asyncio.Lock()
            self._loop = current_loop
        return self._lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        """
        Return the cached value for *key*, or ``None`` if absent / expired.

        Expired entries are pruned lazily on this call.

        Parameters
        ----------
        key:
            Cache key to look up.

        Returns
        -------
        Any | None
            The stored value, or ``None`` when the key is missing or stale.
        """
        async with self.lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() >= expiry:
                # Prune the expired key immediately.
                del self._store[key]
                return None
            return value

    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 600,
    ) -> None:
        """
        Store *value* under *key* with the given TTL.

        If capacity is reached, first expired items are pruned;
        if still at max_size, the oldest inserted key is evicted.
        """
        now = time.monotonic()
        expiry = now + ttl_seconds
        async with self.lock:
            # If inserting a new key at capacity, prune expired keys first
            if key not in self._store and len(self._store) >= self._max_size:
                expired = [k for k, (_, exp) in self._store.items() if now >= exp]
                for k in expired:
                    del self._store[k]

                # If still full, evict oldest entry (FIFO)
                if len(self._store) >= self._max_size:
                    oldest_key = next(iter(self._store))
                    del self._store[oldest_key]

            self._store[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        """
        Remove *key* from the cache (no-op if the key does not exist).

        Parameters
        ----------
        key:
            Cache key to delete.
        """
        async with self.lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        """Remove **all** entries from the cache."""
        async with self.lock:
            self._store.clear()

    # ------------------------------------------------------------------
    # Convenience / introspection helpers
    # ------------------------------------------------------------------

    async def size(self) -> int:
        """
        Return the number of **non-expired** entries currently in the cache.

        This is an O(n) operation as it must check every stored expiry.
        Use only for diagnostics / health-check endpoints.
        """
        now = time.monotonic()
        async with self.lock:
            return sum(
                1
                for _, expiry in self._store.values()
                if now < expiry
            )

    def __repr__(self) -> str:  # pragma: no cover
        return f"TTLCache(entries={len(self._store)})"

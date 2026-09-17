"""
In-memory sliding window rate limiter for protecting sensitive endpoints (e.g. login).

Thread-safe and coroutine-safe using asyncio.Lock, with automatic sliding window cleanup.
Requires zero external dependencies (no Redis needed for single-node deployments).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from sport5_fantasy_api.core.config import settings


class AsyncRateLimiter:
    """
    Sliding-window in-memory rate limiter per client identifier (e.g. client IP).

    Parameters
    ----------
    max_requests:
        Maximum number of requests permitted within the window.
    window_seconds:
        Sliding window duration in seconds (default: 60.0s).
    """

    def __init__(self, max_requests: int = 5, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock tied to the active running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock is None or (current_loop is not None and self._loop != current_loop):
            self._lock = asyncio.Lock()
            self._loop = current_loop
        return self._lock

    async def is_allowed(self, identifier: str) -> tuple[bool, float]:
        """
        Check whether the request for *identifier* is allowed.

        Returns
        -------
        tuple[bool, float]
            (is_allowed, retry_after_seconds)
            - When permitted: (True, 0.0)
            - When rate-limited: (False, retry_after_seconds)
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self.lock:
            timestamps = [t for t in self._history[identifier] if t > cutoff]
            if len(timestamps) >= self.max_requests:
                oldest = timestamps[0]
                retry_after = max(0.1, (oldest + self.window_seconds) - now)
                self._history[identifier] = timestamps
                return False, retry_after

            timestamps.append(now)
            self._history[identifier] = timestamps
            return True, 0.0

    async def reset(self) -> None:
        """Clear all rate limit history."""
        async with self.lock:
            self._history.clear()


# Default singleton limiter for sensitive auth endpoints
login_rate_limiter = AsyncRateLimiter(
    max_requests=settings.rate_limit_login_requests,
    window_seconds=settings.rate_limit_login_window_seconds,
)

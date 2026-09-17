"""
Retry and exponential backoff configuration for Sport5 connectors.

Provides randomized jitter calculations and classification of transient vs.
fail-fast HTTP errors and exceptions.
"""

from __future__ import annotations

import random
from typing import Final

import httpx

# ---------------------------------------------------------------------------
# Constants & Retry Configuration
# ---------------------------------------------------------------------------

RETRY_MAX_ATTEMPTS: Final[int] = 3
RETRY_BASE_DELAY: Final[float] = 0.5
RETRY_BACKOFF_FACTOR: Final[float] = 2.0
RETRY_MAX_DELAY: Final[float] = 2.5

RETRY_STATUS_CODES: Final[frozenset[int]] = frozenset({502, 503, 504})
FAIL_FAST_STATUS_CODES: Final[frozenset[int]] = frozenset({400, 401, 403, 404, 422})

TRANSIENT_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.TimeoutException,
)

# Backwards compatibility aliases
_RETRY_MAX_ATTEMPTS = RETRY_MAX_ATTEMPTS
_RETRY_BASE_DELAY = RETRY_BASE_DELAY
_RETRY_BACKOFF_FACTOR = RETRY_BACKOFF_FACTOR
_RETRY_MAX_DELAY = RETRY_MAX_DELAY
_RETRY_STATUS_CODES = RETRY_STATUS_CODES
_FAIL_FAST_STATUS_CODES = FAIL_FAST_STATUS_CODES
_TRANSIENT_EXCEPTIONS = TRANSIENT_EXCEPTIONS


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = RETRY_BASE_DELAY,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
    max_delay: float = RETRY_MAX_DELAY,
) -> float:
    """
    Compute exponential backoff delay with random jitter.

    Formula: min(base_delay * (backoff_factor ** attempt) + uniform(0.05, 0.15), max_delay)
    """
    jitter = random.uniform(0.05, 0.15)
    delay = base_delay * (backoff_factor**attempt) + jitter
    return min(delay, max_delay)


# Backwards compatibility alias
_calculate_backoff_delay = calculate_backoff_delay

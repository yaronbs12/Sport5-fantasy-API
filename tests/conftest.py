"""
Shared pytest fixtures and test configuration.
"""

from __future__ import annotations

import pytest

from sport5_fantasy_api.core.rate_limit import login_rate_limiter


@pytest.fixture(autouse=True)
def reset_rate_limit_history() -> None:
    """Reset rate limiter state before each test case to avoid cross-test pollution."""
    login_rate_limiter._history.clear()

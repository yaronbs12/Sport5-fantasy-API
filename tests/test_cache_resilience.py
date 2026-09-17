"""
Cache TTL expiry and concurrency safety tests.

Covers:
- Key expiry after TTL elapses
- Concurrency safety under 50 simultaneous get/set calls
- Cache isolation for private (user-specific) routes
- Cache clear behaviour
- Cache size introspection
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.models.user import UserTeamResponse

# ---------------------------------------------------------------------------
# B1. TTL expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_key_expires_after_ttl() -> None:
    """A key stored with ttl_seconds=1 must be gone after time advances past expiry."""
    cache = TTLCache()
    await cache.set("test_key", "hello", ttl_seconds=1)

    # Immediately readable
    assert await cache.get("test_key") == "hello"

    # Patch time.monotonic to simulate expiry
    with patch("sport5_fantasy_api.core.cache.time") as mock_time:
        mock_time.monotonic.return_value = time.monotonic() + 2.0
        result = await cache.get("test_key")

    assert result is None


@pytest.mark.asyncio
async def test_cache_key_within_ttl_still_readable() -> None:
    """A key stored with ttl_seconds=600 is still readable immediately."""
    cache = TTLCache()
    await cache.set("alive", [1, 2, 3], ttl_seconds=600)
    result = await cache.get("alive")
    assert result == [1, 2, 3]


@pytest.mark.asyncio
async def test_cache_missing_key_returns_none() -> None:
    """Getting a key that was never set returns None."""
    cache = TTLCache()
    assert await cache.get("nonexistent") is None


@pytest.mark.asyncio
async def test_cache_delete_removes_key() -> None:
    """Deleting a key makes it unavailable."""
    cache = TTLCache()
    await cache.set("to_delete", 42, ttl_seconds=60)
    await cache.delete("to_delete")
    assert await cache.get("to_delete") is None


@pytest.mark.asyncio
async def test_cache_delete_nonexistent_key_is_noop() -> None:
    """Deleting a non-existent key does not raise."""
    cache = TTLCache()
    await cache.delete("never_existed")  # must not raise


@pytest.mark.asyncio
async def test_cache_clear_removes_all_keys() -> None:
    """clear() removes all stored entries."""
    cache = TTLCache()
    await cache.set("a", 1, ttl_seconds=600)
    await cache.set("b", 2, ttl_seconds=600)
    await cache.clear()
    assert await cache.get("a") is None
    assert await cache.get("b") is None


@pytest.mark.asyncio
async def test_cache_size_counts_live_entries() -> None:
    """size() returns count of non-expired entries."""
    cache = TTLCache()
    await cache.set("x", 1, ttl_seconds=600)
    await cache.set("y", 2, ttl_seconds=600)
    assert await cache.size() == 2


@pytest.mark.asyncio
async def test_cache_overwrite_updates_value() -> None:
    """Setting the same key twice stores the latest value."""
    cache = TTLCache()
    await cache.set("k", "first", ttl_seconds=60)
    await cache.set("k", "second", ttl_seconds=60)
    assert await cache.get("k") == "second"


# ---------------------------------------------------------------------------
# B2. Concurrency safety (50 simultaneous get/set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_concurrent_set_get_no_race_condition() -> None:
    """
    Fire 50 concurrent set() and 50 concurrent get() calls to verify
    the internal asyncio.Lock prevents any data corruption or exceptions.
    """
    cache = TTLCache()

    async def writer(i: int) -> None:
        await cache.set(f"key_{i}", f"value_{i}", ttl_seconds=60)

    async def reader(i: int) -> object:
        return await cache.get(f"key_{i}")

    # First, write all 50 keys concurrently
    await asyncio.gather(*[writer(i) for i in range(50)])

    # Then, read all 50 keys concurrently
    results = await asyncio.gather(*[reader(i) for i in range(50)])

    # All writes must have succeeded
    for i, val in enumerate(results):
        assert val == f"value_{i}", f"key_{i} returned {val!r}"


@pytest.mark.asyncio
async def test_cache_mixed_concurrent_operations_no_exception() -> None:
    """
    Interleaved writes, reads, and deletes from 30 concurrent tasks
    must not raise any exceptions.
    """
    cache = TTLCache()

    async def mixed_ops(i: int) -> None:
        await cache.set(f"m_{i}", i, ttl_seconds=30)
        await cache.get(f"m_{i}")
        if i % 3 == 0:
            await cache.delete(f"m_{i}")

    await asyncio.gather(*[mixed_ops(i) for i in range(30)])
    # If we reach here without exception, the test passes


# ---------------------------------------------------------------------------
# B3. Private routes must not touch shared TTLCache (user data never cached)
# ---------------------------------------------------------------------------


def test_private_me_team_bypasses_cache() -> None:
    """
    Verify that GET /me/team always hits the upstream connector and never
    reads from or writes to the shared TTLCache (user data must not be cached).
    """
    app = create_app()

    mock_team = UserTeamResponse(
        user_id="u42",
        user_name="Test User",
        team_name="My Fantasy",
        budget_remaining=12.5,
        starters=[],
        bench=[],
        captain=None,
        sub_captain=None,
    )

    call_count = 0

    async def fake_get_user_team(
        self: object,
        auth_cookie: str,
        user_id: object = None,
    ) -> UserTeamResponse:
        nonlocal call_count
        call_count += 1
        return mock_team

    with (
        TestClient(app) as client,
        patch(
            "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
            new=fake_get_user_team,
        ),
    ):
        # Call twice -- both must hit connector (no cache)
        r1 = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer fake_token"},
        )
        r2 = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer fake_token"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Connector must have been called once per request (not cached)
    assert call_count == 2


def test_private_me_leagues_bypasses_cache() -> None:
    """GET /me/leagues must hit connector on every request (not cached)."""
    app = create_app()

    call_count = 0

    async def fake_get_user_leagues(self: object, auth_cookie: str) -> list:
        nonlocal call_count
        call_count += 1
        return []

    with (
        TestClient(app) as client,
        patch(
            "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_leagues",
            new=fake_get_user_leagues,
        ),
    ):
        for _ in range(3):
            client.get(
                "/api/v1/israel/me/leagues",
                headers={"X-Sport5-Session": "token"},
            )

    assert call_count == 3

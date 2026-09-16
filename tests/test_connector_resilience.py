"""
Tests for BaseSport5Connector resilience: exponential backoff retry loop,
transient error recovery, jitter, sleep cap, and strict fail-fast behavior.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sport5_fantasy_api.connectors.base import (
    _RETRY_BACKOFF_FACTOR,
    _RETRY_BASE_DELAY,
    _RETRY_MAX_ATTEMPTS,
    _RETRY_MAX_DELAY,
    _calculate_backoff_delay,
)
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)

_WAF_HTML = "<!DOCTYPE html><html><body>WAF DataDome Block</body></html>"


@pytest.fixture(autouse=True)
def mock_asyncio_sleep() -> Generator[AsyncMock, None, None]:
    """Patch asyncio.sleep to execute instantly while recording calls."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


# ---------------------------------------------------------------------------
# 1. Backoff delay calculation unit tests
# ---------------------------------------------------------------------------


def test_backoff_delay_exponential_growth_and_cap() -> None:
    """Verify exponential backoff calculation, jitter addition, and maximum delay cap."""
    for attempt in range(3):
        expected_base = _RETRY_BASE_DELAY * (_RETRY_BACKOFF_FACTOR**attempt)
        delay = _calculate_backoff_delay(attempt)
        # Jitter is in [0.05, 0.15]
        assert expected_base + 0.05 <= delay <= expected_base + 0.15

    # Large attempt number must be capped at _RETRY_MAX_DELAY (2.5s)
    capped_delay = _calculate_backoff_delay(10)
    assert capped_delay == _RETRY_MAX_DELAY


# ---------------------------------------------------------------------------
# 2. Transient HTTP status code retry (502, 503, 504)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_503_twice_then_success(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock transient 503 twice followed by 200 OK -> verify success on 3rd attempt."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")

    resp_503 = httpx.Response(503, request=req, text="Service Unavailable")
    resp_200 = httpx.Response(200, request=req, json={"data": {"success": True}})

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.side_effect = [resp_503, resp_503, resp_200]
        mock_get_client.return_value = mock_client

        data = await connector._safe_get("/api/test")

    assert data == {"data": {"success": True}}
    assert mock_client.get.call_count == 3
    assert mock_asyncio_sleep.call_count == 2


@pytest.mark.asyncio
async def test_retry_persistent_503_exhausts_retries(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Mock persistent 503 -> verify retries exhausted and Sport5UpstreamError raised."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_503 = httpx.Response(503, request=req, text="Service Unavailable")

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = resp_503
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5UpstreamError) as exc_info:
            await connector._safe_get("/api/test")

    assert exc_info.value.status_code == 503
    # 1 initial attempt + 3 retries = 4 attempts total
    assert mock_client.get.call_count == _RETRY_MAX_ATTEMPTS + 1
    assert mock_asyncio_sleep.call_count == _RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_retry_502_and_504_status_codes(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock 502 Bad Gateway and 504 Gateway Timeout -> verify retry recovery."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")

    resp_502 = httpx.Response(502, request=req, text="Bad Gateway")
    resp_504 = httpx.Response(504, request=req, text="Gateway Timeout")
    resp_200 = httpx.Response(200, request=req, json={"data": "recovered"})

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.side_effect = [resp_502, resp_504, resp_200]
        mock_get_client.return_value = mock_client

        data = await connector._safe_get("/api/test")

    assert data == {"data": "recovered"}
    assert mock_client.get.call_count == 3
    assert mock_asyncio_sleep.call_count == 2


# ---------------------------------------------------------------------------
# 3. Transient network exceptions (ReadTimeout, ConnectTimeout, ConnectError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_read_timeout_recovers(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock httpx.ReadTimeout followed by 200 OK -> verify recovery."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_200 = httpx.Response(200, request=req, json={"data": "timeout_recovered"})

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.side_effect = [httpx.ReadTimeout("timed out"), resp_200]
        mock_get_client.return_value = mock_client

        data = await connector._safe_get("/api/test")

    assert data == {"data": "timeout_recovered"}
    assert mock_client.get.call_count == 2
    assert mock_asyncio_sleep.call_count == 1


@pytest.mark.asyncio
async def test_retry_read_timeout_exhausts_retries(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Mock persistent ReadTimeout -> verify exhaustion and Sport5UpstreamError."""
    connector = IsraeliLeagueConnector()

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ReadTimeout("read timeout")
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5UpstreamError, match="timed out"):
            await connector._safe_get("/api/test")

    assert mock_client.get.call_count == _RETRY_MAX_ATTEMPTS + 1
    assert mock_asyncio_sleep.call_count == _RETRY_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_retry_connect_error_and_connect_timeout(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Mock ConnectTimeout and ConnectError -> verify retry and recovery."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_200 = httpx.Response(200, request=req, json={"status": "connected"})

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            httpx.ConnectTimeout("conn timeout"),
            httpx.ConnectError("conn refused"),
            resp_200,
        ]
        mock_get_client.return_value = mock_client

        data = await connector._safe_get("/api/test")

    assert data == {"status": "connected"}
    assert mock_client.get.call_count == 3
    assert mock_asyncio_sleep.call_count == 2


# ---------------------------------------------------------------------------
# 4. Strict Fail-Fast (DO NOT retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_fast_401_no_retry(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock HTTP 401 -> verify zero retry attempts (fail-fast on 1st attempt)."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_401 = httpx.Response(401, request=req, text="Unauthorized")

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = resp_401
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_get("/api/test")

    assert mock_client.get.call_count == 1
    assert mock_asyncio_sleep.call_count == 0


@pytest.mark.asyncio
async def test_fail_fast_403_no_retry(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock HTTP 403 -> verify zero retry attempts."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_403 = httpx.Response(403, request=req, text="Forbidden")

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = resp_403
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_get("/api/test")

    assert mock_client.get.call_count == 1
    assert mock_asyncio_sleep.call_count == 0


@pytest.mark.asyncio
async def test_fail_fast_waf_html_no_retry(mock_asyncio_sleep: AsyncMock) -> None:
    """Mock HTTP 200 with HTML body (WAF block) -> verify zero retry attempts."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    resp_waf = httpx.Response(200, request=req, text=_WAF_HTML)

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = resp_waf
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5WAFBlockError):
            await connector._safe_get("/api/test")

    assert mock_client.get.call_count == 1
    assert mock_asyncio_sleep.call_count == 0


@pytest.mark.asyncio
async def test_fail_fast_waf_html_with_auth_cookie(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Mock HTTP 200 with HTML on authenticated call -> verify immediate Sport5AuthError."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/private")
    resp_waf = httpx.Response(200, request=req, text=_WAF_HTML)

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = resp_waf
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_get("/api/private", auth_cookie="session_token")

    assert mock_client.get.call_count == 1
    assert mock_asyncio_sleep.call_count == 0


@pytest.mark.asyncio
async def test_fail_fast_client_error_400_and_404(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Mock 400 Bad Request and 404 Not Found -> verify zero retries."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")

    for status_code in (400, 404, 422):
        resp = httpx.Response(status_code, request=req, text="Client Error")
        with patch.object(connector, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_get_client.return_value = mock_client

            with pytest.raises(Sport5UpstreamError) as exc_info:
                await connector._safe_get("/api/test")

            assert exc_info.value.status_code == status_code
            assert mock_client.get.call_count == 1
            assert mock_asyncio_sleep.call_count == 0


# ---------------------------------------------------------------------------
# 5. _safe_post retry and fail-fast tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_post_retry_on_503_then_success(
    mock_asyncio_sleep: AsyncMock,
) -> None:
    """Verify _safe_post retries transient 503 and recovers on 2nd attempt."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("POST", "https://dreamteam.sport5.co.il/api/test")
    resp_503 = httpx.Response(503, request=req, text="Unavailable")
    resp_200 = httpx.Response(200, request=req, json={"posted": True})

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.side_effect = [resp_503, resp_200]
        mock_get_client.return_value = mock_client

        res = await connector._safe_post("/api/test", json_data={"key": "val"})

    assert res == {"posted": True}
    assert mock_client.post.call_count == 2
    assert mock_asyncio_sleep.call_count == 1


@pytest.mark.asyncio
async def test_safe_post_fail_fast_on_401(mock_asyncio_sleep: AsyncMock) -> None:
    """Verify _safe_post fails fast on 401 Unauthorized without retries."""
    connector = IsraeliLeagueConnector()
    req = httpx.Request("POST", "https://dreamteam.sport5.co.il/api/test")
    resp_401 = httpx.Response(401, request=req, text="Unauthorized")

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = resp_401
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_post("/api/test")

    assert mock_client.post.call_count == 1
    assert mock_asyncio_sleep.call_count == 0

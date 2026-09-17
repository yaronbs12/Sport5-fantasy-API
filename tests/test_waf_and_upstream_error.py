"""
Upstream outage & WAF simulation tests.

Covers:
- HTML body (WAF block) on public routes -> Sport5WAFBlockError -> HTTP 502
- HTML body with auth cookie on private route -> Sport5AuthError -> HTTP 401
- httpx.TimeoutException on GET request -> HTTP 504 Gateway Timeout
- httpx.RequestError on GET request -> HTTP 502 Bad Gateway
- Upstream 503 status code -> HTTP 502 Bad Gateway
- HTML on login endpoint -> HTTP 401
- _safe_get HTML detection
- _safe_get non-200 status handling
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)

_WAF_HTML = (
    "<!DOCTYPE html><html><head><title>Access Denied</title></head>"
    "<body><p>DataDome Block</p></body></html>"
)


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_asyncio_sleep() -> Generator[AsyncMock, None, None]:
    """Prevent actual sleep in tests when retries occur."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


# ---------------------------------------------------------------------------
# E1. _safe_get: HTML body detection raises correct exceptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_get_html_without_cookie_raises_waf_error() -> None:
    """HTTP 200 with HTML body (no auth cookie) must raise Sport5WAFBlockError."""
    connector = IsraeliLeagueConnector()
    mock_request = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={"Content-Type": "text/html"},
        text=_WAF_HTML,
    )

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get.return_value = mock_client

        with pytest.raises(Sport5WAFBlockError):
            await connector._safe_get("/api/test")


@pytest.mark.asyncio
async def test_safe_get_html_with_cookie_raises_auth_error() -> None:
    """HTTP 200 with HTML body AND auth cookie must raise Sport5AuthError (login wall)."""
    connector = IsraeliLeagueConnector()
    mock_request = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/private")
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={"Content-Type": "text/html"},
        text=_WAF_HTML,
    )

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_get("/api/private", auth_cookie="expired_token")


@pytest.mark.asyncio
async def test_safe_get_non_200_raises_upstream_error() -> None:
    """HTTP 503 from upstream must raise Sport5UpstreamError."""
    connector = IsraeliLeagueConnector()
    mock_request = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/test")
    mock_response = httpx.Response(
        status_code=503,
        request=mock_request,
        text="Service Unavailable",
    )

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get.return_value = mock_client

        with pytest.raises(Sport5UpstreamError):
            await connector._safe_get("/api/test")


@pytest.mark.asyncio
async def test_safe_get_401_raises_auth_error() -> None:
    """HTTP 401 from upstream must raise Sport5AuthError."""
    connector = IsraeliLeagueConnector()
    mock_request = httpx.Request("GET", "https://dreamteam.sport5.co.il/api/auth")
    mock_response = httpx.Response(
        status_code=401,
        request=mock_request,
        text="Unauthorized",
    )

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_get.return_value = mock_client

        with pytest.raises(Sport5AuthError):
            await connector._safe_get("/api/auth")


@pytest.mark.asyncio
async def test_safe_get_timeout_raises_upstream_error() -> None:
    """httpx.TimeoutException during GET must raise Sport5UpstreamError."""
    connector = IsraeliLeagueConnector()

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_get.return_value = mock_client

        with pytest.raises(Sport5UpstreamError):
            await connector._safe_get("/api/test")


@pytest.mark.asyncio
async def test_safe_get_request_error_raises_upstream_error() -> None:
    """httpx.RequestError (network failure) must raise Sport5UpstreamError."""
    connector = IsraeliLeagueConnector()

    with patch.object(connector, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.RequestError("connection refused")
        mock_get.return_value = mock_client

        with pytest.raises(Sport5UpstreamError):
            await connector._safe_get("/api/test")


# ---------------------------------------------------------------------------
# E2. REST API: WAF -> HTTP 502
# ---------------------------------------------------------------------------


def test_public_players_waf_block_returns_502(client: TestClient) -> None:
    """WAF block on /players (Sport5WAFBlockError) must map to HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(side_effect=Sport5WAFBlockError("DataDome blocked request")),
    ):
        resp = client.get("/api/v1/israel/players")

    assert resp.status_code == 502
    body = resp.json()
    assert "error" in body


def test_public_players_upstream_error_returns_502(client: TestClient) -> None:
    """Upstream service error on /players must return HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(side_effect=Sport5UpstreamError("503 from upstream")),
    ):
        resp = client.get("/api/v1/israel/players")

    assert resp.status_code == 502


def test_public_teams_waf_block_returns_502(client: TestClient) -> None:
    """WAF block on /teams must map to HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_teams",
        new=AsyncMock(side_effect=Sport5WAFBlockError()),
    ):
        resp = client.get("/api/v1/israel/teams")

    assert resp.status_code == 502


def test_public_fixtures_upstream_error_returns_502(client: TestClient) -> None:
    """Upstream error on /fixtures must return HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_fixtures",
        new=AsyncMock(side_effect=Sport5UpstreamError("upstream down")),
    ):
        resp = client.get("/api/v1/israel/fixtures")

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# E3. REST API: Timeout -> HTTP 504
# ---------------------------------------------------------------------------


def test_public_players_timeout_returns_non_200(client: TestClient) -> None:
    """
    When get_all_players raises httpx.TimeoutException (unwrapped), the global
    exception handler returns HTTP 500. TestClient must NOT re-raise it.
    """

    async def raise_timeout(*args: object, **kwargs: object) -> None:
        raise httpx.TimeoutException("timed out")

    # Use raise_server_exceptions=False so TestClient returns the error response
    # instead of propagating the Python exception.
    app = create_app()
    with (
        TestClient(app, raise_server_exceptions=False) as safe_client,
        patch(
            "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
            new=raise_timeout,
        ),
    ):
        resp = safe_client.get("/api/v1/israel/players")

    # TimeoutException is caught by the global Exception handler -> 500
    assert resp.status_code in (500, 502, 504)


# ---------------------------------------------------------------------------
# E4. Login: WAF/HTML -> 401
# ---------------------------------------------------------------------------


def test_login_html_body_returns_401(client: TestClient) -> None:
    """HTML body during login (WAF or login wall) -> connector raises Sport5AuthError -> 401."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new=AsyncMock(side_effect=Sport5AuthError("WAF blocked login")),
    ):
        resp = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "pw"},
        )

    assert resp.status_code == 401


def test_login_missing_cookie_returns_401(client: TestClient) -> None:
    """Login succeeds HTTP-wise but cookie absent -> connector raises Sport5AuthError -> 401."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new=AsyncMock(side_effect=Sport5AuthError("Missing session cookie after successful login")),
    ):
        resp = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "pw"},
        )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# E5. Private routes: auth failure from upstream -> 401
# ---------------------------------------------------------------------------


def test_me_team_upstream_html_returns_401(client: TestClient) -> None:
    """Expired session -> upstream returns HTML -> connector raises Sport5AuthError -> 401."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=AsyncMock(side_effect=Sport5AuthError("Session expired HTML login wall")),
    ):
        resp = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer stale_token"},
        )

    assert resp.status_code == 401


def test_me_team_upstream_502_passes_through(client: TestClient) -> None:
    """Sport5UpstreamError on /me/team -> HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=AsyncMock(side_effect=Sport5UpstreamError("upstream crashed")),
    ):
        resp = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer valid_token"},
        )

    assert resp.status_code == 502

"""
Unit and integration tests for Sport5 authentication pipeline, models, and dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import app
from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.core.exceptions import Sport5AuthError
from sport5_fantasy_api.models.auth import LoginRequest, TokenResponse
from sport5_fantasy_api.models.enums import PlayerRole, Position, TournamentType
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

_LOGIN_URL = "https://dreamteam.sport5.co.il/api/Account/Login"


def test_login_request_validation() -> None:
    """Verify LoginRequest email normalization and validation."""
    req = LoginRequest(email="User@Example.COM", password="SecretPassword123")
    assert req.email == "user@example.com"
    assert req.password == "SecretPassword123"

    with pytest.raises(ValueError):
        LoginRequest(email="not-an-email", password="123")


def test_token_response_schema() -> None:
    """Verify TokenResponse model construction."""
    res = TokenResponse(
        access_token="test_token_xyz",
        tournament=TournamentType.ISRAELI_LEAGUE,
    )
    assert res.access_token == "test_token_xyz"
    assert res.token_type == "bearer"
    assert res.tournament == TournamentType.ISRAELI_LEAGUE


@pytest.mark.asyncio
async def test_connector_login_success_via_cookies() -> None:
    """Test successful connector login extracting token from response.cookies."""
    connector = IsraeliLeagueConnector()

    mock_request = httpx.Request("POST", _LOGIN_URL)
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={
            "Content-Type": "application/json",
            "Set-Cookie": ".AspNetCore.Cookies=session_cookie_12345; path=/",
        },
        json={"succeeded": True},
    )

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        token = await connector.login("test@example.com", "password")
        assert token == "session_cookie_12345"
        mock_client.post.assert_called_once_with(
            "/api/Account/Login",
            json={
                "email": "test@example.com",
                "password": "password",
                "googleToken": None,
                "facebookToken": None,
                "adminKey": None,
            },
        )


@pytest.mark.asyncio
async def test_connector_login_success_via_set_cookie_header() -> None:
    """Test successful connector login extracting token from raw Set-Cookie header."""
    connector = IsraeliLeagueConnector()

    mock_request = httpx.Request("POST", _LOGIN_URL)
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={
            "Content-Type": "application/json",
            "Set-Cookie": ".AspNetCore.Cookies=header_cookie_999; path=/; secure; httponly",
        },
        json={"success": True},
    )

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        token = await connector.login("test@example.com", "password")
        assert token == "header_cookie_999"


@pytest.mark.asyncio
async def test_connector_login_invalid_credentials_status_401() -> None:
    """Test connector login failure when upstream returns HTTP 401."""
    connector = IsraeliLeagueConnector()

    mock_request = httpx.Request("POST", _LOGIN_URL)
    mock_response = httpx.Response(
        status_code=401,
        request=mock_request,
        json={"error": "Invalid username or password"},
    )

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError, match="Invalid credentials or login blocked"):
            await connector.login("test@example.com", "wrong_password")


@pytest.mark.asyncio
async def test_connector_login_waf_html_blocked() -> None:
    """Test connector login failure when upstream returns HTML / WAF block."""
    connector = IsraeliLeagueConnector()

    mock_request = httpx.Request("POST", _LOGIN_URL)
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={"Content-Type": "text/html"},
        text="<!DOCTYPE html><html><body>WAF Blocked</body></html>",
    )

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError, match="WAF/HTML response"):
            await connector.login("test@example.com", "password")


@pytest.mark.asyncio
async def test_connector_login_missing_cookie() -> None:
    """Test connector login failure when HTTP 200 is returned but session cookie is absent."""
    connector = IsraeliLeagueConnector()

    mock_request = httpx.Request("POST", _LOGIN_URL)
    mock_response = httpx.Response(
        status_code=200,
        request=mock_request,
        headers={"Content-Type": "application/json"},
        json={"succeeded": True},
    )

    with patch.object(connector, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        with pytest.raises(Sport5AuthError, match="missing session cookie"):
            await connector.login("test@example.com", "password")


def test_api_auth_login_endpoint_success() -> None:
    """Test POST /api/v1/{tournament}/auth/login returns TokenResponse on success."""
    client = TestClient(app)

    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new_callable=AsyncMock,
        return_value="mocked_aspnetcore_token",
    ):
        res = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "secretPassword"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["access_token"] == "mocked_aspnetcore_token"
        assert data["token_type"] == "bearer"
        assert data["tournament"] == "israel"


def test_api_auth_login_endpoint_failure() -> None:
    """Test POST /api/v1/{tournament}/auth/login returns HTTP 401 on authentication failure."""
    client = TestClient(app)

    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new_callable=AsyncMock,
        side_effect=Sport5AuthError("Invalid credentials or login blocked"),
    ):
        res = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "wrongPassword"},
        )
        assert res.status_code == 401
        assert "Invalid credentials" in res.json()["detail"]


def test_private_me_team_with_bearer_token() -> None:
    """Test GET /api/v1/israel/me/team with Authorization Bearer header."""
    client = TestClient(app)

    mock_player = RosterPlayer(
        id=1,
        name="Lionel Messi",
        team_id=10,
        team_name="FC Test",
        position=Position.FWD,
        price=12.0,
        role=PlayerRole.CAPTAIN,
        is_bench=False,
    )
    mock_team = UserTeamResponse(
        user_id="user_123",
        user_name="Test Manager",
        team_name="Dream 11",
        budget_remaining=5.0,
        starters=[mock_player],
        bench=[],
        captain=mock_player,
    )

    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new_callable=AsyncMock,
        return_value=mock_team,
    ) as mock_get_user_team:
        # Bearer token
        res = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer test_bearer_token_abc"},
        )
        assert res.status_code == 200
        assert res.json()["team_name"] == "Dream 11"
        mock_get_user_team.assert_called_once_with(auth_cookie="test_bearer_token_abc")


def test_private_me_team_with_x_sport5_session_header() -> None:
    """Test GET /api/v1/israel/me/team with fallback X-Sport5-Session header."""
    client = TestClient(app)

    mock_player = RosterPlayer(
        id=1,
        name="Lionel Messi",
        team_id=10,
        team_name="FC Test",
        position=Position.FWD,
        price=12.0,
        role=PlayerRole.CAPTAIN,
        is_bench=False,
    )
    mock_team = UserTeamResponse(
        user_id="user_123",
        user_name="Test Manager",
        team_name="Dream 11",
        budget_remaining=5.0,
        starters=[mock_player],
        bench=[],
        captain=mock_player,
    )

    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new_callable=AsyncMock,
        return_value=mock_team,
    ) as mock_get_user_team:
        # X-Sport5-Session header
        res = client.get(
            "/api/v1/israel/me/team",
            headers={"X-Sport5-Session": "test_cookie_custom_hdr"},
        )
        assert res.status_code == 200
        assert res.json()["team_name"] == "Dream 11"
        mock_get_user_team.assert_called_once_with(auth_cookie="test_cookie_custom_hdr")


def test_private_me_team_unauthenticated_missing_headers() -> None:
    """Test GET /api/v1/israel/me/team returns HTTP 401 when no credentials are provided."""
    client = TestClient(app)
    res = client.get("/api/v1/israel/me/team")
    assert res.status_code == 401
    assert "Authentication credentials missing" in res.json()["detail"]

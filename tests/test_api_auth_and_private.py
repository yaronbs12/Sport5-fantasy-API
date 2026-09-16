"""
Authentication & private endpoint tests.

Covers:
- POST /auth/login: valid credentials, invalid credentials, upstream network failure
- GET /me/team: Bearer token, X-Sport5-Session, missing auth, expired session
- Roster role validation: 11 starters + 4 bench, captain/sub_captain roles
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.core.exceptions import Sport5AuthError, Sport5UpstreamError
from sport5_fantasy_api.models.enums import PlayerRole
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

_LOGIN_URL = "https://dreamteam.sport5.co.il/api/Account/Login"


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _make_roster_player(
    pid: int,
    is_bench: bool = False,
    role: PlayerRole = PlayerRole.PLAYER,
) -> RosterPlayer:
    return RosterPlayer.model_validate({
        "playerId": pid,
        "playerName": f"Player {pid}",
        "teamId": 1,
        "teamName": "Team",
        "positionId": 3,
        "price": 7.0,
        "isActive": True,
        "isReserve": is_bench,
        "isCaptain": role == PlayerRole.CAPTAIN,
        "isSubCaptain": role == PlayerRole.SUB_CAPTAIN,
    })


def _make_full_squad() -> UserTeamResponse:
    """Build a valid 11+4 squad with captain and sub_captain."""
    starters = [
        _make_roster_player(i, is_bench=False) for i in range(1, 11)
    ]
    captain = _make_roster_player(100, is_bench=False, role=PlayerRole.CAPTAIN)
    sub_cap = _make_roster_player(200, is_bench=True, role=PlayerRole.SUB_CAPTAIN)
    bench = [
        _make_roster_player(300 + i, is_bench=True) for i in range(3)
    ] + [sub_cap]

    return UserTeamResponse(
        user_id="u1",
        user_name="Yaron",
        team_name="Dream Team",
        budget_remaining=5.0,
        starters=[*starters, captain],
        bench=bench,
        captain=captain,
        sub_captain=sub_cap,
    )


# ---------------------------------------------------------------------------
# D1. Login endpoint
# ---------------------------------------------------------------------------


def test_login_valid_credentials_returns_token(client: TestClient) -> None:
    """Valid credentials: upstream 200 + Set-Cookie -> 200 TokenResponse."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new=AsyncMock(return_value="fake_session_token"),
    ):
        resp = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "correct_pw"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "fake_session_token"
    assert data["token_type"] == "bearer"
    assert data["tournament"] == "israel"


def test_login_invalid_credentials_returns_401(client: TestClient) -> None:
    """Invalid credentials: connector raises Sport5AuthError -> HTTP 401."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new=AsyncMock(side_effect=Sport5AuthError("Invalid credentials")),
    ):
        resp = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "wrong"},
        )

    assert resp.status_code == 401


def test_login_upstream_timeout_returns_502(client: TestClient) -> None:
    """Upstream timeout: connector raises Sport5UpstreamError -> HTTP 502."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.login",
        new=AsyncMock(side_effect=Sport5UpstreamError("Login timed out")),
    ):
        resp = client.post(
            "/api/v1/israel/auth/login",
            json={"email": "user@example.com", "password": "pw"},
        )

    assert resp.status_code == 502


def test_login_malformed_email_returns_422(client: TestClient) -> None:
    """Email validation failure -> HTTP 422 Unprocessable Entity."""
    resp = client.post(
        "/api/v1/israel/auth/login",
        json={"email": "not-an-email", "password": "pw"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# D2. GET /me/team - authentication header variations
# ---------------------------------------------------------------------------


def test_me_team_with_bearer_token(client: TestClient) -> None:
    """Authorization: Bearer <token> must pass and return 200 squad."""
    squad = _make_full_squad()
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=AsyncMock(return_value=squad),
    ):
        resp = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer valid_bearer_token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_name"] == "Yaron"


def test_me_team_with_x_sport5_session_header(client: TestClient) -> None:
    """X-Sport5-Session: <token> must also be accepted."""
    squad = _make_full_squad()
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=AsyncMock(return_value=squad),
    ):
        resp = client.get(
            "/api/v1/israel/me/team",
            headers={"X-Sport5-Session": "valid_session_token"},
        )

    assert resp.status_code == 200


def test_me_team_missing_auth_returns_401(client: TestClient) -> None:
    """No authentication header -> HTTP 401."""
    resp = client.get("/api/v1/israel/me/team")
    assert resp.status_code == 401


def test_me_team_expired_session_returns_401(client: TestClient) -> None:
    """Expired/invalid session (upstream HTML login wall) -> HTTP 401."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=AsyncMock(side_effect=Sport5AuthError("Session expired, HTML login wall")),
    ):
        resp = client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer expired_token"},
        )

    assert resp.status_code == 401


def test_me_team_bearer_token_forwarded_to_connector() -> None:
    """Verify the exact token from the Bearer header is passed to get_user_team."""
    app = create_app()
    captured_tokens: list[str] = []

    async def fake_get_user_team(
        self: object,
        auth_cookie: str,
        user_id: object = None,
    ) -> UserTeamResponse:
        captured_tokens.append(auth_cookie)
        return _make_full_squad()

    with TestClient(app) as client, patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_user_team",
        new=fake_get_user_team,
    ):
        client.get(
            "/api/v1/israel/me/team",
            headers={"Authorization": "Bearer my_actual_token"},
        )

    assert captured_tokens == ["my_actual_token"]


# ---------------------------------------------------------------------------
# D3. Roster role validation
# ---------------------------------------------------------------------------


def test_squad_starter_count_is_eleven() -> None:
    """A valid squad must have exactly 11 starters."""
    squad = _make_full_squad()
    assert len(squad.starters) == 11


def test_squad_bench_count_is_four() -> None:
    """A valid squad must have exactly 4 bench players."""
    squad = _make_full_squad()
    assert len(squad.bench) == 4


def test_captain_has_correct_role() -> None:
    """captain field must have role == CAPTAIN."""
    squad = _make_full_squad()
    assert squad.captain is not None
    assert squad.captain.role == PlayerRole.CAPTAIN
    assert squad.captain.is_captain is True


def test_sub_captain_has_correct_role() -> None:
    """sub_captain field must have role == SUB_CAPTAIN."""
    squad = _make_full_squad()
    assert squad.sub_captain is not None
    assert squad.sub_captain.role == PlayerRole.SUB_CAPTAIN
    assert squad.sub_captain.is_sub_captain is True


def test_roster_player_derive_role_from_is_captain_flag() -> None:
    """isCaptain=True must derive role=CAPTAIN."""
    p = _make_roster_player(1, role=PlayerRole.CAPTAIN)
    assert p.role == PlayerRole.CAPTAIN
    assert p.is_captain is True
    assert p.is_sub_captain is False


def test_roster_player_default_role_is_player() -> None:
    """Default role must be PLAYER when neither isCaptain nor isSubCaptain."""
    p = _make_roster_player(1)
    assert p.role == PlayerRole.PLAYER
    assert p.is_captain is False
    assert p.is_sub_captain is False


def test_me_leagues_missing_auth_returns_401(client: TestClient) -> None:
    """GET /me/leagues without auth must return 401."""
    resp = client.get("/api/v1/israel/me/leagues")
    assert resp.status_code == 401

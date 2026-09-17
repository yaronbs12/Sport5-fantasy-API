"""
Unit tests for extracted modular components:
- retry.py: backoff and jitter calculations
- http_client.py: Sport5HttpClient lifecycle and networking
- parsers: players, fixtures, and user roster data parsing
- rate_limit.py: sliding window rate limiter
- environment and production configuration validation
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.connectors.http_client import Sport5HttpClient
from sport5_fantasy_api.connectors.parsers.fixtures import parse_fixtures_payload
from sport5_fantasy_api.connectors.parsers.players import (
    extract_raw_players,
    parse_players_list,
)
from sport5_fantasy_api.connectors.parsers.user import (
    parse_leaderboard_payload,
    parse_user_leagues_payload,
    parse_user_team_payload,
)
from sport5_fantasy_api.connectors.retry import (
    calculate_backoff_delay,
)
from sport5_fantasy_api.core.config import Settings
from sport5_fantasy_api.core.exceptions import Sport5DataError
from sport5_fantasy_api.core.rate_limit import AsyncRateLimiter, login_rate_limiter
from sport5_fantasy_api.models.enums import PlayerRole

# ---------------------------------------------------------------------------
# 1. Backoff calculation unit tests
# ---------------------------------------------------------------------------


def test_calculate_backoff_delay_bounds() -> None:
    delay = calculate_backoff_delay(0, base_delay=1.0, backoff_factor=2.0, max_delay=10.0)
    assert 1.05 <= delay <= 1.15

    capped = calculate_backoff_delay(10, base_delay=1.0, backoff_factor=2.0, max_delay=5.0)
    assert capped == 5.0


# ---------------------------------------------------------------------------
# 2. Sport5HttpClient lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_client_lifecycle() -> None:
    client_wrapper = Sport5HttpClient("https://example.com", timeout_seconds=5.0)
    assert client_wrapper._client is None

    client = await client_wrapper.get_client()
    assert isinstance(client, httpx.AsyncClient)
    assert not client.is_closed

    # Second call reuses client
    client2 = await client_wrapper.get_client()
    assert client2 is client

    await client_wrapper.aclose()
    assert client.is_closed


# ---------------------------------------------------------------------------
# 3. Parsers: Players
# ---------------------------------------------------------------------------


def test_extract_raw_players_all_shapes() -> None:
    # Shape A: dict with "players"
    payload_a = {"data": {"players": [{"id": 1, "name": "A"}]}}
    res_a = extract_raw_players(payload_a)
    assert len(res_a) == 1
    assert res_a[0]["name"] == "A"

    # Shape B: dict with "teams" -> nested "players"
    payload_b = {
        "data": {
            "teams": [
                {
                    "teamId": 10,
                    "teamName": "Maccabi",
                    "players": [{"id": 2, "name": "B"}],
                }
            ]
        }
    }
    res_b = extract_raw_players(payload_b)
    assert len(res_b) == 1
    assert res_b[0]["teamId"] == 10
    assert res_b[0]["teamName"] == "Maccabi"

    # Shape C: list of dicts
    payload_c = [{"id": 3, "name": "C"}]
    res_c = extract_raw_players(payload_c)
    assert len(res_c) == 1


def test_parse_players_list_active_and_deleted_flags() -> None:
    raw = [
        # Normal active player
        {
            "playerId": 1,
            "playerName": "P1",
            "teamId": 1,
            "teamName": "Team 1",
            "positionId": 3,
            "price": 5.0,
        },
        # Deleted player -> should resolve to is_active=False
        {
            "playerId": 2,
            "playerName": "P2",
            "teamId": 1,
            "teamName": "Team 1",
            "positionId": 3,
            "price": 5.0,
            "isDeleted": True,
        },
        # Malformed record -> should be skipped without exception
        "not a dict",
        {"playerId": "invalid_id_not_an_int"},
    ]
    players = parse_players_list(raw, tournament_tag="test")
    assert len(players) == 2
    assert players[0].id == 1
    assert players[0].is_active is True
    assert players[1].id == 2
    assert players[1].is_active is False


# ---------------------------------------------------------------------------
# 4. Parsers: Fixtures
# ---------------------------------------------------------------------------


def test_parse_fixtures_payload_sorting_and_deadline() -> None:
    league_data = {
        "seasonName": "2025/2026",
        "currentRound": 2,
        "exchangeEndDate": "2026-09-20T18:00:00",
        "allRounds": [
            {
                "id": 2,
                "roundIndex": 2,
                "roundName": "Round 2",
                "startDate": "2026-09-20T18:00:00",
                "endDate": "2026-09-22T23:59:59",
            },
            {
                "id": 1,
                "roundIndex": 1,
                "roundName": "Round 1",
                "startDate": "2026-09-13T18:00:00",
                "endDate": "2026-09-15T23:59:59",
            },
        ],
        "games": [
            {
                "id": 102,
                "roundId": 2,
                "teamAId": 1,
                "teamAName": "Team 1",
                "teamBId": 2,
                "teamBName": "Team 2",
                "gameStart": "2026-09-21T20:00:00",
            },
            {
                "id": 101,
                "roundId": 1,
                "teamAId": 3,
                "teamAName": "Team 3",
                "teamBId": 4,
                "teamBName": "Team 4",
                "gameStart": "2026-09-20T19:00:00",
            },
        ],
    }
    meta = parse_fixtures_payload(league_data, season_id=10, tournament_tag="test")
    assert meta.season_name == "2025/2026"
    assert meta.current_round == 2
    assert meta.exchange_deadline == datetime(2026, 9, 20, 18, 0, 0)
    # Rounds must be sorted by round_index (1, 2)
    assert meta.rounds[0].round_index == 1
    assert meta.rounds[1].round_index == 2
    # Games must be sorted chronologically (101 at 19:00 before 102 at 20:00)
    assert meta.games[0].id == 101
    assert meta.games[1].id == 102


# ---------------------------------------------------------------------------
# 5. Parsers: User squad & leagues
# ---------------------------------------------------------------------------


def test_parse_user_team_payload() -> None:
    payload = {
        "data": {
            "userTeam": {
                "userId": "user_42",
                "userName": "Yaron",
                "teamName": "Dreamers",
                "budget": 12.5,
                "userTeamPlayers": [
                    {
                        "playerId": 1,
                        "playerName": "Starter 1",
                        "teamId": 10,
                        "teamName": "Team A",
                        "positionId": 3,
                        "price": 8.0,
                        "isReserve": False,
                        "isCaptain": True,
                    },
                    {
                        "playerId": 2,
                        "playerName": "Bench 1",
                        "teamId": 10,
                        "teamName": "Team A",
                        "positionId": 1,
                        "price": 4.5,
                        "isReserve": True,
                        "isCaptain": False,
                    },
                ],
            }
        }
    }
    team = parse_user_team_payload(payload, tournament_tag="test")
    assert team.user_id == "user_42"
    assert team.budget_remaining == 12.5
    assert len(team.starters) == 1
    assert len(team.bench) == 1
    assert team.captain is not None
    assert team.captain.id == 1
    assert team.captain.role == PlayerRole.CAPTAIN


def test_parse_user_team_payload_missing_user_team() -> None:
    with pytest.raises(Sport5DataError):
        parse_user_team_payload({"data": {}})


def test_parse_user_leagues_and_leaderboard() -> None:
    leagues_payload = {
        "data": {
            "leagues": [
                {"id": 100, "name": "Work League", "membersCount": 15},
                {"id": 101, "name": "Friends League", "membersCount": 8},
            ]
        }
    }
    leagues = parse_user_leagues_payload(leagues_payload, tournament_tag="test")
    assert len(leagues) == 2
    assert leagues[0].name == "Work League"

    leaderboard_payload = {
        "data": {
            "leagueId": 100,
            "leagueName": "Work League",
            "totalMembers": 2,
            "pageIndex": 0,
            "members": [
                {
                    "userId": "1",
                    "userName": "Player 1",
                    "teamName": "T1",
                    "totalPoints": 150,
                    "rank": 1,
                },
                {
                    "userId": "2",
                    "userName": "Player 2",
                    "teamName": "T2",
                    "totalPoints": 120,
                    "rank": 2,
                },
            ],
        }
    }
    board = parse_leaderboard_payload(leaderboard_payload, league_id=100, tournament_tag="test")
    assert board.league_id == 100
    assert len(board.members) == 2
    assert board.members[0].rank == 1


# ---------------------------------------------------------------------------
# 6. Rate Limiter Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_rate_limiter_sliding_window() -> None:
    limiter = AsyncRateLimiter(max_requests=2, window_seconds=1.0)
    await limiter.reset()

    allowed, retry_after = await limiter.is_allowed("ip_1")
    assert allowed is True
    assert retry_after == 0.0

    allowed, retry_after = await limiter.is_allowed("ip_1")
    assert allowed is True
    assert retry_after == 0.0

    # 3rd request in same window should be rejected
    allowed, retry_after = await limiter.is_allowed("ip_1")
    assert allowed is False
    assert retry_after > 0.0

    # Different IP should not be affected
    allowed_other, _ = await limiter.is_allowed("ip_2")
    assert allowed_other is True


def test_login_rate_limiting_http_429() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Reset limiter state
        login_rate_limiter._history.clear()

        # Perform 5 allowed logins (mocked to fail auth quickly)
        with patch("sport5_fantasy_api.connectors.base.BaseSport5Connector.login") as mock_login:
            from sport5_fantasy_api.core.exceptions import Sport5AuthError

            mock_login.side_effect = Sport5AuthError("Invalid credentials")

            for _ in range(5):
                resp = client.post(
                    "/api/v1/israel/auth/login",
                    json={"email": "test@example.com", "password": "pass"},
                )
                assert resp.status_code == 401

            # 6th attempt should hit HTTP 429 Too Many Requests
            resp_429 = client.post(
                "/api/v1/israel/auth/login",
                json={"email": "test@example.com", "password": "pass"},
            )
            assert resp_429.status_code == 429
            assert "Retry-After" in resp_429.headers
            assert "Too many login attempts" in resp_429.json()["detail"]


# ---------------------------------------------------------------------------
# 7. Environment & Configuration Settings
# ---------------------------------------------------------------------------


def test_settings_environment_and_cors_options() -> None:
    dev_settings = Settings(
        environment="development", cors_origins="http://localhost:3000,http://localhost:5173"
    )
    assert dev_settings.environment == "development"
    assert dev_settings.cors_origins == ["http://localhost:3000", "http://localhost:5173"]
    assert dev_settings.rate_limit_enabled is True

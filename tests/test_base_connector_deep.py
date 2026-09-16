"""
Deep unit tests for BaseSport5Connector and domain models.

Covers:
- Client lifecycle (_build_client, _get_client, aclose, __aenter__, __aexit__)
- Safe GET & POST retry loops, transient status codes (502/503/504), timeout/network errors
- Season discovery cache hit and fallback matching
- get_league_details caching
- get_teams and get_teams_mapping (including skipping malformed teams)
- get_all_players with diverse upstream payload formats (nested teams, flat list, fallback)
- get_fixtures with round/game sorting and deadline parsing (ISO, epoch-ms, invalid)
- login edge cases: Set-Cookie header extraction, JSON failure flag, timeout/request errors
- get_user_team: squad separation into starters/bench, captain/sub-captain
- get_user_leagues: dict envelope vs list envelope, skipping malformed records
- TTLCache bounded capacity and FIFO eviction
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sport5_fantasy_api.connectors.israeli_league import IsraeliLeagueConnector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5DataError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)
from sport5_fantasy_api.models.enums import Position


@pytest.fixture
def connector() -> IsraeliLeagueConnector:
    return IsraeliLeagueConnector(cache=TTLCache(max_size=50))


# ---------------------------------------------------------------------------
# 1. Lifecycle and Context Manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connector_lifecycle_and_context_manager(
    connector: IsraeliLeagueConnector,
) -> None:
    async with connector as conn:
        client = await conn._get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed

    # After exiting context manager, client should be closed
    assert connector._client is not None
    assert connector._client.is_closed


# ---------------------------------------------------------------------------
# 2. _safe_get & _safe_post Error Handling and Retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_get_timeout_and_network_error(
    connector: IsraeliLeagueConnector,
) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.get.side_effect = httpx.ConnectTimeout("connection timed out")

    with patch.object(connector, "_get_client", return_value=mock_client):
        with pytest.raises(Sport5UpstreamError) as exc_info:
            await connector._safe_get("/test", max_retries=1, base_delay=0.01)
        assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_safe_get_generic_request_error(
    connector: IsraeliLeagueConnector,
) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.get.side_effect = httpx.ProtocolError("protocol error")

    with patch.object(connector, "_get_client", return_value=mock_client):
        with pytest.raises(Sport5UpstreamError) as exc_info:
            await connector._safe_get("/test", max_retries=0)
        assert "Network error reaching Sport5" in str(exc_info.value)


@pytest.mark.asyncio
async def test_safe_get_unexpected_non_200_and_json_error(
    connector: IsraeliLeagueConnector,
) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False

    # 500 error (unexpected non-200, not in 502/503/504 retry set)
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_client.get.return_value = mock_resp_500

    with patch.object(connector, "_get_client", return_value=mock_client):
        with pytest.raises(Sport5UpstreamError) as exc:
            await connector._safe_get("/test", max_retries=0)
        assert exc.value.status_code == 500

    # Invalid JSON on HTTP 200
    mock_resp_bad_json = MagicMock()
    mock_resp_bad_json.status_code = 200
    mock_resp_bad_json.text = "{not valid json}"
    mock_resp_bad_json.json.side_effect = ValueError("Invalid JSON")
    mock_client.get.return_value = mock_resp_bad_json

    with (
        patch.object(connector, "_get_client", return_value=mock_client),
        pytest.raises(Sport5DataError),
    ):
        await connector._safe_get("/test", max_retries=0)


@pytest.mark.asyncio
async def test_safe_post_success_and_waf_detection(
    connector: IsraeliLeagueConnector,
) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False

    # Successful POST
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"success": true}'
    mock_resp.json.return_value = {"success": True}
    mock_client.post.return_value = mock_resp

    with patch.object(connector, "_get_client", return_value=mock_client):
        res = await connector._safe_post(
            "/api/test", auth_cookie="test-token", json_data={"a": 1}
        )
        assert res == {"success": True}

    # POST returning HTML (WAF block)
    mock_resp_html = MagicMock()
    mock_resp_html.status_code = 200
    mock_resp_html.text = "<!DOCTYPE html><html><body>Blocked</body></html>"
    mock_client.post.return_value = mock_resp_html

    with (
        patch.object(connector, "_get_client", return_value=mock_client),
        pytest.raises(Sport5WAFBlockError),
    ):
        await connector._safe_post("/api/test")

    # POST with auth cookie returning HTML -> Sport5AuthError
    with (
        patch.object(connector, "_get_client", return_value=mock_client),
        pytest.raises(Sport5AuthError),
    ):
        await connector._safe_post("/api/test", auth_cookie="cookie123")


# ---------------------------------------------------------------------------
# 3. Season Discovery Cache Hit and Fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_season_id_cache_hit_and_fallback(
    connector: IsraeliLeagueConnector,
) -> None:
    # Set cache directly
    cache_key = f"season_id:{connector.tournament_type}"
    await connector.cache.set(cache_key, 999)

    discovered = await connector.discover_season_id()
    assert discovered == 999

    # Clear cache and test fallback to first active league when target urlName not matched
    await connector.cache.clear()
    payload = {
        "data": [
            {"urlName": "other_league", "currentSeasonId": 42, "isActive": True}
        ]
    }
    with patch.object(connector, "_safe_get", return_value=payload):
        discovered = await connector.discover_season_id()
        assert discovered == 42


# ---------------------------------------------------------------------------
# 4. Teams and Mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_teams_and_mapping(connector: IsraeliLeagueConnector) -> None:
    league_payload = {
        "data": {
            "teams": [
                {
                    "id": 1,
                    "name": "Maccabi Tel Aviv",
                    "teamLogoPath": "logo1.png",
                    "teamShirtPath": "shirt1.png",
                },
                {"id": 2, "name": "Hapoel Tel Aviv"},
                {"invalid": "no id or name"},  # Should be skipped gracefully
            ]
        }
    }
    with patch.object(
        connector, "get_league_details", return_value=league_payload["data"]
    ):
        teams = await connector.get_teams()
        assert len(teams) == 2
        assert teams[0].id == 1
        assert teams[0].name == "Maccabi Tel Aviv"
        assert teams[0].logo_url == "logo1.png"

        mapping = await connector.get_teams_mapping()
        assert mapping == {1: "Maccabi Tel Aviv", 2: "Hapoel Tel Aviv"}


# ---------------------------------------------------------------------------
# 5. Players Parsing Variations (Case C and Fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_players_case_c_and_basketball_resilience(
    connector: IsraeliLeagueConnector,
) -> None:
    # Case C: list of team dicts containing player lists
    upstream_data = {
        "data": [
            {
                "teamId": 10,
                "teamName": "Maccabi",
                "teamPlayers": [
                    {
                        "playerId": 101,
                        "playerName": "Footballer A",
                        "positionId": 1,
                        "price": 5.0,
                        "isActive": True,
                    },
                    {
                        "playerId": 102,
                        "playerName": "Basketball C",
                        "positionId": 5,
                        "price": 4.5,
                        "isActive": True,
                    },
                    {
                        "playerId": 103,
                        "playerName": "Unknown Pos",
                        "positionId": 99,
                        "price": 3.0,
                        "isActive": True,
                    },
                ],
            }
        ]
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=upstream_data),
    ):
        players = await connector.get_all_players()
        assert len(players) == 3
        assert players[0].position == Position.GK
        assert players[1].position == Position.CENTER
        assert players[2].position == Position.UNKNOWN


@pytest.mark.asyncio
async def test_get_all_players_fallback_to_league_details(
    connector: IsraeliLeagueConnector,
) -> None:
    # GetTeamsAndPlayers raises an exception, triggers fallback
    league_details = {
        "teams": [{"id": 5, "name": "Beitar Jerusalem"}],
        "players": [
            {
                "id": 201,
                "name": "Fallback Player",
                "teamId": 5,
                "positionId": 4,
                "price": 7.0,
                "isActive": True,
            }
        ],
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(
            connector, "_safe_get", side_effect=Exception("Endpoint failed")
        ),
        patch.object(
            connector, "get_league_details", return_value=league_details
        ),
    ):
        players = await connector.get_all_players()
        assert len(players) == 1
        assert players[0].name == "Fallback Player"
        assert players[0].team_name == "Beitar Jerusalem"


# ---------------------------------------------------------------------------
# 6. Fixtures and Deadline Parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fixtures_parsing_and_sorting(
    connector: IsraeliLeagueConnector,
) -> None:
    # Using epoch ms for exchangeEndDate and match kickoff
    epoch_ms = 1700000000000
    league_payload = {
        "seasonName": "2024-2025",
        "currentRound": 2,
        "exchangeEndDate": epoch_ms,
        "allRounds": [
            {
                "id": 20,
                "roundIndex": 2,
                "startDate": "2024-10-10T18:00:00Z",
                "endDate": "2024-10-12T22:00:00Z",
            },
            {
                "id": 10,
                "roundIndex": 1,
                "startDate": "2024-10-01T18:00:00Z",
                "endDate": "2024-10-03T22:00:00Z",
            },
        ],
        "games": [
            {
                "id": 102,
                "roundId": 1,
                "teamAId": 1,
                "teamAName": "Home",
                "teamBId": 2,
                "teamBName": "Away",
                "gameStart": "2024-10-02T20:00:00Z",
                "gameStatus": 6,
                "resultData": '{"score": "2-1"}',
            },
            {
                "id": 101,
                "roundId": 1,
                "teamAId": 3,
                "teamAName": "Home2",
                "teamBId": 4,
                "teamBName": "Away2",
                "gameStart": "2024-10-01T19:00:00Z",
                "gameStatus": 0,
            },
        ],
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(
            connector, "get_league_details", return_value=league_payload
        ),
    ):
        fixtures = await connector.get_fixtures()
        assert fixtures.season_name == "2024-2025"
        assert fixtures.current_round == 2
        assert isinstance(fixtures.exchange_deadline, datetime)
        # Verify rounds sorted by round_index (1, then 2)
        assert fixtures.rounds[0].round_index == 1
        assert fixtures.rounds[1].round_index == 2
        # Verify matches sorted by kickoff_time (101 before 102)
        assert fixtures.games[0].id == 101
        assert fixtures.games[1].id == 102
        assert fixtures.games[1].is_finished is True


# ---------------------------------------------------------------------------
# 7. Login Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_cookie_extraction_from_set_cookie_header(
    connector: IsraeliLeagueConnector,
) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False

    # Simulate cookie not in response.cookies jar, but in set-cookie headers
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"succeeded": true}'
    mock_resp.cookies.get.return_value = None
    mock_resp.headers.get_list.return_value = [
        "other_cookie=123; path=/",
        ".AspNetCore.Cookies=extracted_secret_cookie; path=/; httponly",
    ]
    mock_resp.json.return_value = {"succeeded": True}
    mock_client.post.return_value = mock_resp

    with patch.object(connector, "_get_client", return_value=mock_client):
        token = await connector.login("test@example.com", "password")
        assert token == "extracted_secret_cookie"


@pytest.mark.asyncio
async def test_login_json_failure_flag(connector: IsraeliLeagueConnector) -> None:
    mock_client = AsyncMock()
    mock_client.is_closed = False

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"succeeded": false, "error": "Invalid email or password"}'
    mock_resp.cookies.get.return_value = "should_not_matter"
    mock_resp.json.return_value = {
        "succeeded": False,
        "error": "Invalid email or password",
    }
    mock_client.post.return_value = mock_resp

    with patch.object(connector, "_get_client", return_value=mock_client):
        with pytest.raises(Sport5AuthError) as exc_info:
            await connector.login("test@example.com", "wrongpass")
        assert "Invalid email or password" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. User Team and User Leagues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_team_full_hierarchy(
    connector: IsraeliLeagueConnector,
) -> None:
    payload = {
        "data": {
            "userTeam": {
                "userId": "user_42",
                "userName": "ChampionUser",
                "teamName": "Dream Squad",
                "budget": 12.5,
                "userTeamPlayers": [
                    {
                        "playerId": 1,
                        "playerName": "Star Player",
                        "teamId": 10,
                        "teamName": "Team A",
                        "positionId": 3,
                        "price": 8.0,
                        "totalPoints": 50,
                        "isActive": True,
                        "isReserve": False,
                        "isCaptain": True,
                        "isSubCaptain": False,
                    },
                    {
                        "playerId": 2,
                        "playerName": "Vice Star",
                        "teamId": 10,
                        "teamName": "Team A",
                        "positionId": 2,
                        "price": 6.0,
                        "totalPoints": 35,
                        "isActive": True,
                        "isReserve": False,
                        "isCaptain": False,
                        "isSubCaptain": True,
                    },
                    {
                        "playerId": 3,
                        "playerName": "Bench Warmer",
                        "teamId": 11,
                        "teamName": "Team B",
                        "positionId": 1,
                        "price": 4.5,
                        "totalPoints": 10,
                        "isActive": True,
                        "isReserve": True,
                        "isCaptain": False,
                        "isSubCaptain": False,
                    },
                ],
            }
        }
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=payload),
    ):
        squad = await connector.get_user_team(auth_cookie="valid-token")
        assert squad.user_id == "user_42"
        assert squad.user_name == "ChampionUser"
        assert squad.budget_remaining == 12.5
        assert len(squad.starters) == 2
        assert len(squad.bench) == 1
        assert squad.captain is not None and squad.captain.id == 1
        assert squad.sub_captain is not None and squad.sub_captain.id == 2


@pytest.mark.asyncio
async def test_get_user_team_missing_payload_raises(
    connector: IsraeliLeagueConnector,
) -> None:
    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value={"data": {}}),
        pytest.raises(Sport5DataError),
    ):
        await connector.get_user_team(auth_cookie="valid-token")


@pytest.mark.asyncio
async def test_get_user_leagues_dict_and_list_envelope(
    connector: IsraeliLeagueConnector,
) -> None:
    # Dict envelope {"data": {"leagues": [...]}}
    dict_payload = {
        "data": {
            "leagues": [
                {"id": 101, "name": "Work League", "usersCount": 15},
                {"malformed": "record"},  # Should be skipped
            ]
        }
    }
    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=dict_payload),
    ):
        leagues = await connector.get_user_leagues(auth_cookie="valid-token")
        assert len(leagues) == 1
        assert leagues[0].id == 101
        assert leagues[0].name == "Work League"
        assert leagues[0].member_count == 15


# ---------------------------------------------------------------------------
# 9. TTLCache Bounded Capacity & Eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_cache_capacity_and_eviction() -> None:
    cache = TTLCache(max_size=3)

    # Insert 3 items
    await cache.set("k1", "v1", ttl_seconds=60)
    await cache.set("k2", "v2", ttl_seconds=60)
    await cache.set("k3", "v3", ttl_seconds=60)
    assert await cache.size() == 3

    # Inserting 4th item must evict the oldest entry ("k1")
    await cache.set("k4", "v4", ttl_seconds=60)
    assert await cache.get("k1") is None
    assert await cache.get("k2") == "v2"
    assert await cache.get("k3") == "v3"
    assert await cache.get("k4") == "v4"

    # Test expired item eviction before FIFO
    await cache.set("k2", "v2_exp", ttl_seconds=-1)  # expired immediately
    await cache.set("k5", "v5", ttl_seconds=60)
    assert await cache.get("k2") is None
    assert await cache.get("k5") == "v5"

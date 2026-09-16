"""
Tests for live players data pipeline, models, connector methods, and REST endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import app
from sport5_fantasy_api.connectors import IsraeliLeagueConnector
from sport5_fantasy_api.models.enums import Position
from sport5_fantasy_api.models.player import Player


def test_player_model_validations() -> None:
    """Verify position enum mapping, name sanitisation, and price/points defaults."""
    raw_gk = {
        "playerId": 101,
        "playerName": "Daniel`Tenenbaum",
        "teamId": 5,
        "teamName": "Maccabi Tel Aviv",
        "positionId": 1,
        "price": 6.5,
        "totalPoints": 42,
        "isActive": True,
    }
    p_gk = Player.model_validate(raw_gk)
    assert p_gk.id == 101
    assert p_gk.name == "Daniel'Tenenbaum"
    assert p_gk.position == Position.GK
    assert p_gk.price == 6.5
    assert p_gk.total_points == 42
    assert p_gk.is_active is True

    # Position integer mappings: 1=GK, 2=DEF, 3=MID, 4=FWD
    assert Player.model_validate({**raw_gk, "positionId": 2}).position == Position.DEF
    assert Player.model_validate({**raw_gk, "positionId": 3}).position == Position.MID
    assert Player.model_validate({**raw_gk, "positionId": 4}).position == Position.FWD


def test_player_flat_and_nested_dict_aliases() -> None:
    """Verify player can be parsed from snake_case or alternative field aliases."""
    raw_snake = {
        "id": 202,
        "name": "Eran`Zahavi",
        "team_id": 5,
        "team_name": "Maccabi",
        "position": "FWD",
        "price": 12.0,
        "points": 150,
    }
    p = Player.model_validate(raw_snake)
    assert p.id == 202
    assert p.name == "Eran'Zahavi"
    assert p.position == Position.FWD
    assert p.total_points == 150


@pytest.mark.asyncio
async def test_connector_get_all_players_flat_structure() -> None:
    """Test get_all_players parsing data.players flat response."""
    connector = IsraeliLeagueConnector()

    mock_data = {
        "data": {
            "players": [
                {
                    "playerId": 1,
                    "playerName": "Player One",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 1,
                    "price": 5.0,
                },
                {
                    "playerId": 2,
                    "playerName": "Player Two",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 3,
                    "price": 8.0,
                },
            ]
        }
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=mock_data) as mock_get,
    ):
        players = await connector.get_all_players()

        assert len(players) == 2
        assert players[0].id == 1
        assert players[0].position == Position.GK
        assert players[1].id == 2
        assert players[1].position == Position.MID

        mock_get.assert_called_once_with(
            "/api/Players/GetTeamsAndPlayers", params={"seasonId": 10}
        )

        # Test cache hit
        cached_players = await connector.get_all_players()
        assert len(cached_players) == 2
        # Second call shouldn't trigger _safe_get again
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_connector_get_all_players_nested_teams_structure() -> None:
    """Test get_all_players parsing data.teams[].players structure."""
    connector = IsraeliLeagueConnector()

    mock_data = {
        "data": {
            "teams": [
                {
                    "id": 10,
                    "name": "Maccabi",
                    "players": [
                        {
                            "id": 11,
                            "name": "Star Striker",
                            "positionId": 4,
                            "price": 10.0,
                        }
                    ],
                }
            ]
        }
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=mock_data),
    ):
        players = await connector.get_all_players()
        assert len(players) == 1
        p = players[0]
        assert p.id == 11
        assert p.name == "Star Striker"
        assert p.team_id == 10
        assert p.team_name == "Maccabi"
        assert p.position == Position.FWD


@pytest.mark.asyncio
async def test_connector_get_player_by_id() -> None:
    """Test get_player_by_id returns matching player or None."""
    connector = IsraeliLeagueConnector()
    dummy_players = [
        Player(
            id=100,
            name="Alice",
            team_id=1,
            team_name="FC A",
            position=Position.GK,
            price=5.0,
        ),
        Player(
            id=200,
            name="Bob",
            team_id=2,
            team_name="FC B",
            position=Position.MID,
            price=7.0,
        ),
    ]

    with patch.object(connector, "get_all_players", return_value=dummy_players):
        p1 = await connector.get_player_by_id(100)
        assert p1 is not None
        assert p1.name == "Alice"

        p_missing = await connector.get_player_by_id(999)
        assert p_missing is None


def test_fastapi_players_endpoints() -> None:
    """Test FastAPI /api/v1/israel/players listing, filtering, and detail endpoints."""
    dummy_players = [
        Player(
            id=1,
            name="Keeper",
            team_id=10,
            team_name="Team A",
            position=Position.GK,
            price=4.5,
        ),
        Player(
            id=2,
            name="Midfielder",
            team_id=10,
            team_name="Team A",
            position=Position.MID,
            price=7.0,
        ),
        Player(
            id=3,
            name="Forward",
            team_id=20,
            team_name="Team B",
            position=Position.FWD,
            price=11.0,
        ),
    ]

    client = TestClient(app)

    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new_callable=AsyncMock,
        return_value=dummy_players,
    ), patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_player_by_id",
        new_callable=AsyncMock,
        side_effect=lambda pid: next((p for p in dummy_players if p.id == pid), None),
    ):
        # Test full list
        res = client.get("/api/v1/israel/players")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 3

        # Test filtering by position
        res_pos = client.get("/api/v1/israel/players?position=MID")
        assert res_pos.status_code == 200
        data_pos = res_pos.json()
        assert len(data_pos) == 1
        assert data_pos[0]["name"] == "Midfielder"

        # Test filtering by min_price & max_price
        res_price = client.get("/api/v1/israel/players?min_price=5.0&max_price=8.0")
        assert res_price.status_code == 200
        data_price = res_price.json()
        assert len(data_price) == 1
        assert data_price[0]["name"] == "Midfielder"

        # Test filtering by team_id
        res_team = client.get("/api/v1/israel/players?team_id=20")
        assert res_team.status_code == 200
        data_team = res_team.json()
        assert len(data_team) == 1
        assert data_team[0]["name"] == "Forward"

        # Test single player endpoint (200 OK)
        res_single = client.get("/api/v1/israel/players/1")
        assert res_single.status_code == 200
        assert res_single.json()["name"] == "Keeper"

        # Test single player endpoint (404 Not Found)
        res_404 = client.get("/api/v1/israel/players/999")
        assert res_404.status_code == 404
        assert res_404.json()["detail"] == "Player with ID 999 not found in israel pool."


@pytest.mark.asyncio
async def test_connector_get_all_players_active_status_resolution() -> None:
    """Test get_all_players dynamic resolution of is_active from isDeleted and isRemoved."""
    connector = IsraeliLeagueConnector()

    mock_data = {
        "data": {
            "players": [
                {
                    "playerId": 1,
                    "playerName": "Active Player",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 1,
                    "price": 5.0,
                    "isActive": True,
                },
                {
                    "playerId": 2,
                    "playerName": "Deleted Player",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 2,
                    "price": 4.5,
                    "isActive": True,
                    "isDeleted": True,
                },
                {
                    "playerId": 3,
                    "playerName": "Removed Player",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 3,
                    "price": 6.0,
                    "isActive": True,
                    "isRemoved": True,
                },
                {
                    "playerId": 4,
                    "playerName": "Inactive Flag Player",
                    "teamId": 10,
                    "teamName": "Team A",
                    "positionId": 4,
                    "price": 8.0,
                    "isActive": False,
                },
            ]
        }
    }

    with (
        patch.object(connector, "discover_season_id", return_value=10),
        patch.object(connector, "_safe_get", return_value=mock_data),
    ):
        players = await connector.get_all_players()
        by_id = {p.id: p for p in players}

        assert by_id[1].is_active is True
        assert by_id[2].is_active is False
        assert by_id[3].is_active is False
        assert by_id[4].is_active is False


"""
Tests for:
  - EuroleagueConnector / WorldCupConnector / EuroConnector basics
  - discover_season_id() urlName-based matching
  - Player.price normalization (>= 100_000 → divide by 1M)
  - Player.is_active with isDeleted / isRemoved flags
  - GET /api/v1/tournaments endpoint
  - GET /api/v1/{tournament}/players?include_inactive=true
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.connectors.euro import EuroConnector
from sport5_fantasy_api.connectors.euroleague import EuroleagueConnector
from sport5_fantasy_api.connectors.world_cup import WorldCupConnector
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.models.enums import TournamentType
from sport5_fantasy_api.models.player import Player

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(**kwargs: object) -> Player:
    """Build a minimal valid Player dict and return the model."""
    base = {
        "playerId": 1,
        "playerName": "Test Player",
        "teamId": 10,
        "teamName": "Test FC",
        "positionId": 3,
        "price": 6.5,
        "isActive": True,
    }
    base.update(kwargs)
    return Player.model_validate(base)


# ---------------------------------------------------------------------------
# 1. New Connector basics
# ---------------------------------------------------------------------------


def test_euroleague_connector_properties() -> None:
    c = EuroleagueConnector()
    assert c.tournament_type == TournamentType.EUROLEAGUE
    assert c.default_season_id == 11
    assert "dreamteam.sport5.co.il" in c.base_url


def test_world_cup_connector_properties() -> None:
    c = WorldCupConnector()
    assert c.tournament_type == TournamentType.WORLD_CUP
    assert c.default_season_id == 9
    assert "dreamteam.sport5.co.il" in c.base_url


def test_euro_connector_properties() -> None:
    c = EuroConnector()
    assert c.tournament_type == TournamentType.EURO
    assert c.default_season_id == 3
    assert "dreamteam.sport5.co.il" in c.base_url


# ---------------------------------------------------------------------------
# 2. discover_season_id — urlName matching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_season_id_matches_url_name() -> None:
    """EuroleagueConnector must pick seasonId=11 via urlName='fantasyeuroleague'."""
    connector = EuroleagueConnector(cache=TTLCache())

    upstream_payload = {
        "data": [
            {"urlName": "dreamteam", "currentSeasonId": 10, "isActive": True},
            {"urlName": "fantasyleague", "currentSeasonId": 12, "isActive": True},
            {"urlName": "fantasyeuroleague", "currentSeasonId": 11, "isActive": True},
            {"urlName": "fantasywc", "currentSeasonId": 9, "isActive": True},
            {"urlName": "eurofantasy", "currentSeasonId": 3, "isActive": False},
        ]
    }

    with patch.object(connector, "_safe_get", new=AsyncMock(return_value=upstream_payload)):
        season_id = await connector.discover_season_id()

    assert season_id == 11


@pytest.mark.asyncio
async def test_discover_season_id_world_cup() -> None:
    """WorldCupConnector must pick seasonId=9 via urlName='fantasywc'."""
    connector = WorldCupConnector(cache=TTLCache())

    upstream_payload = {
        "data": [
            {"urlName": "dreamteam", "currentSeasonId": 10, "isActive": True},
            {"urlName": "fantasywc", "currentSeasonId": 9, "isActive": True},
        ]
    }

    with patch.object(connector, "_safe_get", new=AsyncMock(return_value=upstream_payload)):
        season_id = await connector.discover_season_id()

    assert season_id == 9


@pytest.mark.asyncio
async def test_discover_season_id_falls_back_on_error() -> None:
    """When discovery fails, connector falls back to default_season_id."""
    connector = EuroleagueConnector(cache=TTLCache())

    err = Exception("network error")
    with patch.object(connector, "_safe_get", new=AsyncMock(side_effect=err)):
        season_id = await connector.discover_season_id()

    assert season_id == connector.default_season_id


# ---------------------------------------------------------------------------
# 3. Player.price normalization
# ---------------------------------------------------------------------------


def test_price_normalisation_large_int() -> None:
    """5_000_000 raw upstream value → 5.0 after normalisation."""
    p = _make_player(price=5_000_000)
    assert p.price == pytest.approx(5.0)


def test_price_normalisation_below_threshold() -> None:
    """Values below 100_000 are passed through unchanged."""
    p = _make_player(price=6.5)
    assert p.price == pytest.approx(6.5)


def test_price_normalisation_exact_threshold() -> None:
    """Exactly 100_000 triggers normalisation → 0.1."""
    p = _make_player(price=100_000)
    assert p.price == pytest.approx(0.1)


def test_price_normalisation_float_sub_units() -> None:
    """A float that is >= 100_000 also normalises."""
    p = _make_player(price=7_500_000.0)
    assert p.price == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# 4. Player.is_active with isDeleted / isRemoved flags
# ---------------------------------------------------------------------------


def test_is_active_true_by_default() -> None:
    p = _make_player(isActive=True)
    assert p.is_active is True


def test_is_active_false_via_isActive() -> None:
    p = _make_player(isActive=False)
    assert p.is_active is False


def test_is_active_parsed_via_active_alias() -> None:
    base = {
        "playerId": 1,
        "playerName": "X",
        "teamId": 1,
        "teamName": "T",
        "positionId": 1,
        "price": 5.0,
        "active": False,
    }
    p = Player.model_validate(base)
    assert p.is_active is False


# ---------------------------------------------------------------------------
# 5. GET /api/v1/tournaments
# ---------------------------------------------------------------------------


def test_get_tournaments_endpoint() -> None:
    if TestClient is None:
        pytest.skip("TestClient not available")

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/v1/tournaments")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 5  # All 5 tournaments

    slugs = {t["tournament"] for t in data}
    assert "israel" in slugs
    assert "champions" in slugs
    assert "euroleague" in slugs
    assert "world-cup" in slugs
    assert "euro" in slugs

    # Euroleague must have sport=Basketball
    euroleague = next(t for t in data if t["tournament"] == "euroleague")
    assert euroleague["sport"] == "Basketball"
    assert euroleague["active"] is True
    assert euroleague["default_season_id"] == 11

    # Euro must be inactive
    euro = next(t for t in data if t["tournament"] == "euro")
    assert euro["active"] is False


# ---------------------------------------------------------------------------
# 6. GET /{tournament}/players?include_inactive
# ---------------------------------------------------------------------------


def test_players_endpoint_excludes_inactive_by_default() -> None:
    if TestClient is None:
        pytest.skip("TestClient not available")

    app = create_app()

    mock_players = [
        Player.model_validate({
            "playerId": 1, "playerName": "Active", "teamId": 1,
            "teamName": "T", "positionId": 2, "price": 5.0, "isActive": True,
        }),
        Player.model_validate({
            "playerId": 2, "playerName": "Inactive", "teamId": 1,
            "teamName": "T", "positionId": 2, "price": 4.0, "isActive": False,
        }),
    ]

    with TestClient(app) as client, patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=mock_players),
    ):
        resp_default = client.get("/api/v1/israel/players")
        resp_include = client.get("/api/v1/israel/players?include_inactive=true")

    assert resp_default.status_code == 200
    default_ids = [p["id"] for p in resp_default.json()]
    assert 1 in default_ids
    assert 2 not in default_ids  # inactive excluded by default

    assert resp_include.status_code == 200
    include_ids = [p["id"] for p in resp_include.json()]
    assert 1 in include_ids
    assert 2 in include_ids  # inactive included when requested

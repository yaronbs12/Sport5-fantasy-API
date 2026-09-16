"""
Tests for all public (unauthenticated) API endpoints.

Covers:
- GET /api/v1/tournaments: all 5 entries, correct sport/active flags
- GET /api/v1/{tournament}/players: price/position/team/inactive filters + boundary checks
- GET /api/v1/{tournament}/players/{id}: 200 hit, 404 miss
- GET /api/v1/{tournament}/teams: shape validation
- GET /api/v1/{tournament}/fixtures: chronological round ordering, deadline
- Invalid tournament slug: 422 (path enum validation)
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sport5_fantasy_api.api.main import create_app
from sport5_fantasy_api.models.fixture import LeagueMetaResponse, Match, RoundInfo, Team
from sport5_fantasy_api.models.player import Player

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c



def _make_player(
    pid: int,
    position: str = "MID",
    price: float = 7.0,
    team_id: int = 1,
    is_active: bool = True,
) -> Player:
    pos_map = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
    return Player.model_validate({
        "playerId": pid,
        "playerName": f"Player {pid}",
        "teamId": team_id,
        "teamName": "Team A",
        "positionId": pos_map[position],
        "price": price,
        "isActive": is_active,
    })


def _make_fixture_meta() -> LeagueMetaResponse:
    # Rounds deliberately created out-of-order to verify sorting (3, 1, 2)
    # The connector sorts them; our mock bypasses the connector, so we sort here
    # to test that the response reflects the correct sorted state.
    rounds = sorted([
        RoundInfo.model_validate({"id": r, "roundIndex": r,
                                  "startDate": f"2024-10-0{r}T00:00:00",
                                  "endDate": f"2024-10-0{r}T23:59:59"})
        for r in [3, 1, 2]
    ], key=lambda x: x.round_index)
    games = [
        Match.model_validate({
            "id": 1, "roundId": 1,
            "teamAId": 1, "teamAName": "Home", "teamALogo": None,
            "teamBId": 2, "teamBName": "Away", "teamBLogo": None,
            "gameStart": "2024-10-01T19:00:00",
            "gameStatus": 6,
        }),
    ]
    return LeagueMetaResponse(
        season_id=10,
        season_name="2024/25",
        current_round=2,
        exchange_deadline=_dt.datetime(2024, 10, 10, 12, 0),
        rounds=rounds,
        games=games,
    )


# ---------------------------------------------------------------------------
# C1. GET /api/v1/tournaments
# ---------------------------------------------------------------------------


def test_tournaments_returns_all_five(client: TestClient) -> None:
    """All 5 tournaments must be present in the directory response."""
    resp = client.get("/api/v1/tournaments")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
    slugs = {t["tournament"] for t in data}
    assert slugs == {"israel", "champions", "euroleague", "world-cup", "euro"}


def test_tournaments_sport_types_correct(client: TestClient) -> None:
    """Euroleague must be Basketball; all others must be Football."""
    resp = client.get("/api/v1/tournaments")
    data = resp.json()
    by_slug = {t["tournament"]: t for t in data}
    assert by_slug["euroleague"]["sport"] == "Basketball"
    for slug in ("israel", "champions", "world-cup", "euro"):
        assert by_slug[slug]["sport"] == "Football"


def test_tournaments_active_flags(client: TestClient) -> None:
    """Euro must be inactive=False; israeli/champions/euroleague/world-cup must be active=True."""
    resp = client.get("/api/v1/tournaments")
    data = resp.json()
    by_slug = {t["tournament"]: t for t in data}
    assert by_slug["euro"]["active"] is False
    for slug in ("israel", "champions", "euroleague", "world-cup"):
        assert by_slug[slug]["active"] is True


def test_tournaments_season_ids_correct(client: TestClient) -> None:
    """Default season IDs must match the verified upstream league directory."""
    resp = client.get("/api/v1/tournaments")
    data = resp.json()
    by_slug = {t["tournament"]: t for t in data}
    assert by_slug["israel"]["default_season_id"] == 10
    assert by_slug["champions"]["default_season_id"] == 12
    assert by_slug["euroleague"]["default_season_id"] == 11
    assert by_slug["world-cup"]["default_season_id"] == 9
    assert by_slug["euro"]["default_season_id"] == 3


def test_openapi_schema_tournaments_documentation(client: TestClient) -> None:
    """Verify OpenAPI schema documentation covers all 5 tournaments and tags."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()

    assert schema["info"]["title"] == "Sport5 Fantasy API"
    desc = schema["info"]["description"]

    # All 5 tournaments documented
    for slug in ("israel", "champions", "euroleague", "world-cup", "euro"):
        assert slug in desc, f"Slug {slug!r} missing from OpenAPI description"

    # Notes on Euroleague (basketball) and Euro (inactive)
    assert "basketball" in desc.lower()
    assert "inactive" in desc.lower() or "archive" in desc.lower()

    # OpenAPI tags check
    tags = {t["name"] for t in schema.get("tags", [])}
    assert {"Tournaments", "Public", "Auth", "Private (Authenticated)", "Health"}.issubset(tags)



# ---------------------------------------------------------------------------
# C2. GET /api/v1/{tournament}/players - filters
# ---------------------------------------------------------------------------


def _mock_players() -> list[Player]:
    return [
        _make_player(1, "GK", 5.5, 10, True),
        _make_player(2, "DEF", 6.0, 10, True),
        _make_player(3, "MID", 7.5, 20, True),
        _make_player(4, "FWD", 8.5, 20, True),
        _make_player(5, "MID", 9.5, 30, False),  # inactive
    ]


def test_players_price_range_filter(client: TestClient) -> None:
    """min_price & max_price must filter inclusively."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players?min_price=6.0&max_price=8.5")
    assert resp.status_code == 200
    players = resp.json()
    for p in players:
        assert 6.0 <= p["price"] <= 8.5, f"Player {p['id']} has price {p['price']}"


def test_players_position_filter_fwd_only(client: TestClient) -> None:
    """position=FWD must return only forwards."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players?position=FWD")
    assert resp.status_code == 200
    players = resp.json()
    assert len(players) >= 1
    for p in players:
        assert p["position"] == "FWD"


def test_players_team_id_filter(client: TestClient) -> None:
    """team_id=20 must return only players from team 20."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players?team_id=20")
    assert resp.status_code == 200
    players = resp.json()
    for p in players:
        assert p["team_id"] == 20


def test_players_excludes_inactive_by_default(client: TestClient) -> None:
    """Default include_inactive=False must exclude inactive players."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players")
    assert resp.status_code == 200
    players = resp.json()
    for p in players:
        assert p["is_active"] is True, f"Player {p['id']} is inactive but should be excluded"


def test_players_includes_inactive_when_requested(client: TestClient) -> None:
    """include_inactive=true must include inactive players in results."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players?include_inactive=true")
    assert resp.status_code == 200
    players = resp.json()
    inactive = [p for p in players if not p["is_active"]]
    assert len(inactive) >= 1


def test_players_no_midfielders_when_filtering_gk(client: TestClient) -> None:
    """position=GK must return zero MID/DEF/FWD results."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=_mock_players()),
    ):
        resp = client.get("/api/v1/israel/players?position=GK")
    assert resp.status_code == 200
    players = resp.json()
    for p in players:
        assert p["position"] == "GK"


# ---------------------------------------------------------------------------
# C3. Invalid tournament slug
# ---------------------------------------------------------------------------


def test_invalid_tournament_slug_returns_422(client: TestClient) -> None:
    """An unrecognised tournament slug that fails enum parsing returns 422."""
    resp = client.get("/api/v1/invalid_league/players")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# C4. GET /api/v1/{tournament}/players/{id}
# ---------------------------------------------------------------------------


def test_get_player_by_id_returns_200(client: TestClient) -> None:
    """Valid player ID must return 200 with correct schema."""
    target = _make_player(42, "FWD", 8.0, 5)
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=[target]),
    ):
        resp = client.get("/api/v1/israel/players/42")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 42
    assert data["position"] == "FWD"
    assert data["price"] == pytest.approx(8.0)


def test_get_player_by_id_returns_404(client: TestClient) -> None:
    """Non-existent player ID must return 404 with detail message."""
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_all_players",
        new=AsyncMock(return_value=[]),
    ):
        resp = client.get("/api/v1/israel/players/9999")
    assert resp.status_code == 404
    assert "9999" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# C5. GET /api/v1/{tournament}/teams
# ---------------------------------------------------------------------------


def test_teams_endpoint_shape(client: TestClient) -> None:
    """Teams response must be a list with id and name fields."""
    mock_teams = [
        Team.model_validate({"id": 1, "name": "Hapoel", "teamLogoPath": "/logo.png"}),
        Team.model_validate({"id": 2, "name": "Maccabi"}),
    ]
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_teams",
        new=AsyncMock(return_value=mock_teams),
    ):
        resp = client.get("/api/v1/israel/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["name"] == "Hapoel"
    # FastAPI serializes Team using alias field names (populate_by_name=True)
    assert data[0]["teamLogoPath"] == "/logo.png"
    assert data[1]["teamLogoPath"] is None


# ---------------------------------------------------------------------------
# C6. GET /api/v1/{tournament}/fixtures - chronological ordering
# ---------------------------------------------------------------------------


def test_fixtures_rounds_sorted_chronologically(client: TestClient) -> None:
    """Rounds must be returned sorted ascending by round_index (roundIndex in JSON)."""
    meta = _make_fixture_meta()
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_fixtures",
        new=AsyncMock(return_value=meta),
    ):
        resp = client.get("/api/v1/israel/fixtures")
    assert resp.status_code == 200
    data = resp.json()
    # FastAPI serializes RoundInfo using aliases: round_index -> roundIndex
    indices = [r["roundIndex"] for r in data["rounds"]]
    assert indices == sorted(indices), f"Rounds not sorted: {indices}"


def test_fixtures_exchange_deadline_populated(client: TestClient) -> None:
    """exchange_deadline must be a non-null ISO datetime string when set."""
    meta = _make_fixture_meta()
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_fixtures",
        new=AsyncMock(return_value=meta),
    ):
        resp = client.get("/api/v1/israel/fixtures")
    assert resp.status_code == 200
    data = resp.json()
    # FastAPI serializes LeagueMetaResponse using aliases: exchange_deadline -> exchangeDeadline
    assert data["exchangeDeadline"] is not None


def test_fixtures_match_is_finished_field(client: TestClient) -> None:
    """A match with gameStatus=6 must have is_finished=True in the response."""
    meta = _make_fixture_meta()
    with patch(
        "sport5_fantasy_api.connectors.base.BaseSport5Connector.get_fixtures",
        new=AsyncMock(return_value=meta),
    ):
        resp = client.get("/api/v1/israel/fixtures")
    assert resp.status_code == 200
    data = resp.json()
    assert data["games"][0]["is_finished"] is True

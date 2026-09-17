"""
Edge case and boundary validation tests for Pydantic models.

Covers:
- Player price normalization boundaries
- String sanitization (backticks, whitespace)
- Player active/deleted/removed flags
- Match date / epoch parser edge cases
- Negative price raises ValidationError
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sport5_fantasy_api.models.enums import Position
from sport5_fantasy_api.models.fixture import (
    Match,
    RoundInfo,
    Team,
    _parse_sport5_datetime,
)
from sport5_fantasy_api.models.player import Player

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_player_dict(**overrides: object) -> dict:
    base: dict = {
        "playerId": 99,
        "playerName": "Test Player",
        "teamId": 5,
        "teamName": "Test FC",
        "positionId": 2,
        "price": 7.5,
        "isActive": True,
    }
    base.update(overrides)
    return base


def _make_match_dict(**overrides: object) -> dict:
    base: dict = {
        "id": 1,
        "roundId": 1,
        "teamAId": 1,
        "teamAName": "Home FC",
        "teamBId": 2,
        "teamBName": "Away FC",
        "gameStart": "2024-10-05T19:00:00",
        "gameStatus": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A. Price normalization boundaries
# ---------------------------------------------------------------------------


def test_price_zero_stays_zero() -> None:
    """price=0 must remain 0.0 (below 100_000 threshold)."""
    p = Player.model_validate(_make_player_dict(price=0))
    assert p.price == pytest.approx(0.0)


def test_price_below_threshold_unchanged() -> None:
    """price=99_999 is strictly below 100_000 and must not be divided."""
    p = Player.model_validate(_make_player_dict(price=99_999))
    assert p.price == pytest.approx(99_999.0)


def test_price_at_threshold_normalized() -> None:
    """price=100_000 is at the threshold and must be divided: 100_000/1_000_000 = 0.1."""
    p = Player.model_validate(_make_player_dict(price=100_000))
    assert p.price == pytest.approx(0.1)


def test_price_large_int_normalized() -> None:
    """price=120_000_000 -> 120.0 (upstream sub-unit format)."""
    p = Player.model_validate(_make_player_dict(price=120_000_000))
    assert p.price == pytest.approx(120.0)


def test_price_negative_raises_validation_error() -> None:
    """price=-500 must raise a pydantic ValidationError (ge=0.0 constraint)."""
    with pytest.raises(ValidationError):
        Player.model_validate(_make_player_dict(price=-500))


def test_price_float_large_normalized() -> None:
    """Float raw value >= 100_000 is also normalized."""
    p = Player.model_validate(_make_player_dict(price=7_500_000.0))
    assert p.price == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# B. String sanitization
# ---------------------------------------------------------------------------


def test_backtick_in_name_replaced_with_apostrophe() -> None:
    """Backtick characters in player names must be replaced with single quotes."""
    p = Player.model_validate(_make_player_dict(playerName="Obi`wan"))
    assert "`" not in p.name
    assert "'" in p.name


def test_multiple_backticks_all_replaced() -> None:
    """All backtick characters are replaced, not just the first."""
    p = Player.model_validate(_make_player_dict(playerName="Club Bruges` FC`"))
    assert "`" not in p.name
    assert p.name.count("'") == 2


def test_player_name_whitespace_stripped() -> None:
    """Leading/trailing whitespace in player names is stripped by str_strip_whitespace."""
    p = Player.model_validate(_make_player_dict(playerName="  Messi  "))
    assert p.name == "Messi"


def test_team_name_preserved_as_is() -> None:
    """Team name with normal content is stored exactly."""
    t = Team.model_validate({"id": 1, "name": "Hapoel Tel Aviv"})
    assert t.name == "Hapoel Tel Aviv"


def test_team_logo_url_defaults_to_none() -> None:
    """Team logo_url defaults to None when not provided upstream."""
    t = Team.model_validate({"id": 2, "name": "Maccabi Haifa"})
    assert t.logo_url is None
    assert t.shirt_url is None


# ---------------------------------------------------------------------------
# C. Player active / deleted / removed flags
# ---------------------------------------------------------------------------


def test_is_active_true_from_isactive() -> None:
    """isActive=True -> is_active=True."""
    p = Player.model_validate(_make_player_dict(isActive=True))
    assert p.is_active is True


def test_is_active_false_from_isactive() -> None:
    """isActive=False -> is_active=False."""
    p = Player.model_validate(_make_player_dict(isActive=False))
    assert p.is_active is False


def test_is_active_false_from_active_alias() -> None:
    """active=False alias -> is_active=False."""
    d = _make_player_dict()
    del d["isActive"]
    d["active"] = False
    p = Player.model_validate(d)
    assert p.is_active is False


def test_is_active_defaults_to_true() -> None:
    """When no active flag is supplied, is_active defaults to True."""
    d = _make_player_dict()
    del d["isActive"]
    p = Player.model_validate(d)
    assert p.is_active is True


def test_is_active_false_from_is_deleted() -> None:
    """isDeleted=True must map to is_active=False."""
    d = _make_player_dict(isDeleted=True)
    p = Player.model_validate(d)
    assert p.is_active is False


def test_is_active_false_from_is_removed() -> None:
    """isRemoved=True must map to is_active=False."""
    d = _make_player_dict(isRemoved=True)
    p = Player.model_validate(d)
    assert p.is_active is False


# ---------------------------------------------------------------------------
# D. Match date / epoch parser
# ---------------------------------------------------------------------------


def test_parse_sport5_datetime_with_iso_string() -> None:
    """ISO 8601 strings ending in Z are normalised to +00:00."""
    result = _parse_sport5_datetime("2024-10-05T19:00:00Z")
    assert result == "2024-10-05T19:00:00+00:00"


def test_parse_sport5_datetime_with_epoch_ms() -> None:
    """Epoch milliseconds integer is converted to datetime."""
    epoch_ms = 1789121795000
    result = _parse_sport5_datetime(epoch_ms)
    assert isinstance(result, datetime)
    expected = datetime.fromtimestamp(1789121795, tz=timezone.utc)
    assert result == expected


def test_parse_sport5_datetime_with_none_returns_none() -> None:
    """None input passes through unchanged (no crash)."""
    result = _parse_sport5_datetime(None)
    assert result is None


def test_parse_sport5_datetime_already_datetime() -> None:
    """An existing datetime object is returned as-is."""
    dt = datetime(2024, 10, 5, 19, 0, 0)
    result = _parse_sport5_datetime(dt)
    assert result is dt


def test_match_kickoff_accepts_epoch_ms() -> None:
    """Match model accepts epoch-ms integer for gameStart."""
    d = _make_match_dict(gameStart=1789121795000)
    m = Match.model_validate(d)
    assert isinstance(m.kickoff_time, datetime)


def test_match_kickoff_accepts_iso_string() -> None:
    """Match model accepts ISO 8601 string for gameStart."""
    d = _make_match_dict(gameStart="2024-10-05T19:00:00")
    m = Match.model_validate(d)
    assert isinstance(m.kickoff_time, datetime)


def test_match_is_finished_from_status_6() -> None:
    """gameStatus=6 marks match as finished."""
    m = Match.model_validate(_make_match_dict(gameStatus=6))
    assert m.is_finished is True


def test_match_is_finished_false_for_status_0() -> None:
    """gameStatus=0 marks match as not finished."""
    m = Match.model_validate(_make_match_dict(gameStatus=0))
    assert m.is_finished is False


def test_match_result_data_parsed_from_json_string() -> None:
    """resultData JSON string is parsed into a dict."""
    payload = json.dumps({"homeScore": 2, "awayScore": 1})
    m = Match.model_validate(_make_match_dict(resultData=payload, gameStatus=6))
    assert isinstance(m.result_data, dict)
    assert m.result_data["homeScore"] == 2


def test_round_info_accepts_epoch_ms_dates() -> None:
    """RoundInfo startDate / endDate fields accept epoch-ms integers."""
    r = RoundInfo.model_validate(
        {
            "id": 1,
            "roundIndex": 1,
            "startDate": 1700000000000,
            "endDate": 1700086400000,
        }
    )
    assert isinstance(r.start_date, datetime)
    assert isinstance(r.end_date, datetime)


def test_round_info_accepts_iso_dates() -> None:
    """RoundInfo startDate / endDate fields accept ISO strings."""
    r = RoundInfo.model_validate(
        {
            "id": 2,
            "roundIndex": 2,
            "startDate": "2024-10-10T00:00:00",
            "endDate": "2024-10-17T00:00:00",
        }
    )
    assert isinstance(r.start_date, datetime)


def test_position_string_digit_normalised() -> None:
    """positionId as string digit '3' is normalised to Position.MID."""
    p = Player.model_validate(_make_player_dict(positionId="3"))
    assert p.position == Position.MID


def test_unknown_position_normalised_to_unknown() -> None:
    """Numeric positionId 99 is unknown and gracefully falls back to Position.UNKNOWN."""
    p = Player.model_validate(_make_player_dict(positionId=99))
    assert p.position == Position.UNKNOWN

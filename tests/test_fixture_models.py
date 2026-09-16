"""
Smoke tests for the updated fixture / team models and connector parsing logic.
Run: python tests/test_fixture_models.py
"""

from __future__ import annotations

import json
from datetime import datetime

from sport5_fantasy_api.models.fixture import (
    LeagueMetaResponse,
    Match,
    RoundInfo,
    Team,
    _parse_sport5_datetime,
)

# ---------------------------------------------------------------------------
# _parse_sport5_datetime helper
# ---------------------------------------------------------------------------

dt = _parse_sport5_datetime(1700000000000)  # epoch ms
assert isinstance(dt, datetime), f"Expected datetime, got {type(dt)}"
print("PASS _parse_sport5_datetime epoch-ms")

dt2 = _parse_sport5_datetime("2024-11-15T18:00:00Z")
assert dt2 == "2024-11-15T18:00:00+00:00"
print("PASS _parse_sport5_datetime ISO Z-normalise")

dt3 = _parse_sport5_datetime("2024-11-15T18:00:00+03:00")
assert dt3 == "2024-11-15T18:00:00+03:00"
print("PASS _parse_sport5_datetime ISO offset passthrough")

# ---------------------------------------------------------------------------
# Team model
# ---------------------------------------------------------------------------

t = Team.model_validate({
    "id": 5,
    "name": "מכבי תל אביב",
    "teamLogoPath": "/images/logos/maccabi.png",
    "teamShirtPath": "/images/shirts/maccabi.png",
})
assert t.id == 5
assert t.name == "מכבי תל אביב"
assert t.logo_url == "/images/logos/maccabi.png"
assert t.shirt_url == "/images/shirts/maccabi.png"
print(f"PASS Team: {t.name} logo={t.logo_url}")

t_nologos = Team.model_validate({"id": 7, "name": "הפועל ת\"א"})
assert t_nologos.logo_url is None
assert t_nologos.shirt_url is None
print(f"PASS Team (no logos): {t_nologos.name}")

# ---------------------------------------------------------------------------
# RoundInfo model
# ---------------------------------------------------------------------------

r = RoundInfo.model_validate({
    "id": 101,
    "roundIndex": 5,
    "startDate": "2024-11-15T18:00:00",
    "endDate": "2024-11-18T22:00:00",
})
assert r.id == 101
assert r.round_index == 5
assert isinstance(r.start_date, datetime)
print(f"PASS RoundInfo: round={r.round_index} start={r.start_date.date()}")

# Test epoch-ms date parsing
r_epoch = RoundInfo.model_validate({
    "id": 102,
    "roundIndex": 6,
    "startDate": 1700000000000,
    "endDate": 1700100000000,
})
assert isinstance(r_epoch.start_date, datetime)
print(f"PASS RoundInfo epoch-ms dates: {r_epoch.start_date}")

# ---------------------------------------------------------------------------
# Match model — normal fixture
# ---------------------------------------------------------------------------

m = Match.model_validate({
    "id": 999,
    "roundId": 101,
    "teamAId": 5,
    "teamAName": "מכבי ת\"א",
    "teamALogo": "/logos/maccabi.png",
    "teamBId": 3,
    "teamBName": "הפועל ב\"ש",
    "teamBLogo": "/logos/hapoel.png",
    "gameStart": "2024-11-16T19:00:00",
    "gameStatus": 0,
    "resultData": None,
})
assert m.id == 999
assert m.home_team_id == 5
assert m.away_team_id == 3
assert not m.is_finished
assert m.result_data is None
print(f"PASS Match (scheduled): {m.home_team_name} vs {m.away_team_name}")

# Match that is finished via gameStatus=6
m_done = Match.model_validate({
    "id": 1000,
    "roundId": 100,
    "teamAId": 1,
    "teamAName": "Team A",
    "teamALogo": None,
    "teamBId": 2,
    "teamBName": "Team B",
    "teamBLogo": None,
    "gameStart": "2024-11-10T19:00:00",
    "gameStatus": 6,
    "resultData": None,
})
assert m_done.is_finished, "gameStatus=6 should mark as finished"
print("PASS Match (finished via gameStatus=6)")

# Match finished via resultData (JSON string)
result_payload = json.dumps({"homeScore": 2, "awayScore": 1, "events": []})
m_result = Match.model_validate({
    "id": 1001,
    "roundId": 100,
    "teamAId": 1,
    "teamAName": "Team A",
    "teamALogo": None,
    "teamBId": 2,
    "teamBName": "Team B",
    "teamBLogo": None,
    "gameStart": "2024-11-10T19:00:00",
    "gameStatus": 0,  # status not updated yet
    "resultData": result_payload,
})
assert m_result.is_finished, "Non-null resultData should mark as finished"
assert isinstance(m_result.result_data, dict)
assert m_result.result_data.get("homeScore") == 2
print(f"PASS Match (finished via resultData): {m_result.result_data}")

# ---------------------------------------------------------------------------
# LeagueMetaResponse
# ---------------------------------------------------------------------------

lmr = LeagueMetaResponse(
    season_id=10,
    season_name="2024/25",
    current_round=5,
    exchange_deadline=datetime(2024, 11, 14, 23, 59, 59),
    rounds=[r, r_epoch],
    games=[m, m_done, m_result],
)
assert lmr.season_id == 10
assert lmr.current_round == 5
assert lmr.exchange_deadline is not None
assert len(lmr.rounds) == 2
assert len(lmr.games) == 3
finished_count = sum(1 for g in lmr.games if g.is_finished)
assert finished_count == 2
print(
    f"PASS LeagueMetaResponse: season={lmr.season_name} "
    f"rounds={len(lmr.rounds)} games={len(lmr.games)} "
    f"finished={finished_count}"
)

# LeagueMetaResponse with no deadline
lmr_no_deadline = LeagueMetaResponse(season_id=1, current_round=0)
assert lmr_no_deadline.exchange_deadline is None
print("PASS LeagueMetaResponse (no deadline)")

print()
print("All fixture model tests PASSED!")

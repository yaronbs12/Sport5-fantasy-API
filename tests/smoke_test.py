"""
Quick smoke-test for all Pydantic models.
Run: python tests/smoke_test.py
"""

from sport5_fantasy_api.models.enums import PlayerRole, Position, TournamentType
from sport5_fantasy_api.models.fixture import LeagueMetaResponse
from sport5_fantasy_api.models.league import LeagueSummary
from sport5_fantasy_api.models.player import Player
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

# --- Player model with camelCase aliases + name sanitizer + numeric position ---
p = Player.model_validate({
    "playerId": 1,
    "playerName": "Test`Player",
    "teamId": 5,
    "teamName": "Maccabi",
    "positionId": 3,
    "price": 7.5,
    "totalPoints": 100,
    "isActive": True,
})
assert p.name == "Test'Player", f"Name sanitizer failed: {p.name}"
assert p.position == Position.MID, f"Position mapping failed: {p.position}"
print("PASS Player model:", p.name, p.position)

# --- RosterPlayer captain derivation ---
rp = RosterPlayer.model_validate({
    "playerId": 1,
    "playerName": "Captain",
    "teamId": 5,
    "teamName": "Maccabi",
    "positionId": 1,
    "price": 6.0,
    "isReserve": False,
    "isCaptain": True,
    "isSubCaptain": False,
})
assert rp.role == PlayerRole.CAPTAIN
assert rp.is_captain
print("PASS RosterPlayer captain:", rp.role)

# --- Vice captain ---
rp2 = RosterPlayer.model_validate({
    "playerId": 2,
    "playerName": "Sub",
    "teamId": 5,
    "teamName": "Maccabi",
    "positionId": 4,
    "price": 5.0,
    "isReserve": True,
    "isCaptain": False,
    "isSubCaptain": True,
})
assert rp2.role == PlayerRole.SUB_CAPTAIN
assert rp2.is_bench
print("PASS RosterPlayer sub-captain + bench:", rp2.role, rp2.is_bench)

# --- LeagueSummary ---
ls = LeagueSummary.model_validate({
    "leagueId": 42,
    "leagueName": "Family League",
    "membersCount": 8,
})
assert ls.id == 42
assert ls.member_count == 8
print("PASS LeagueSummary:", ls.name, ls.member_count)

# --- LeagueMetaResponse ---
fr = LeagueMetaResponse.model_validate({
    "seasonId": 10,
    "currentRound": 5,
    "teams": [{"id": 1, "name": "Maccabi"}],
    "rounds": [
        {
            "id": 5,
            "roundIndex": 5,
            "startDate": "2024-11-15T18:00:00",
            "endDate": "2024-11-18T22:00:00",
        }
    ],
    "games": [],
})
assert fr.season_id == 10
assert len(fr.rounds) == 1
print(f"PASS LeagueMetaResponse: seasonId={fr.season_id} rounds={len(fr.rounds)}")

# --- UserTeamResponse direct construction ---
utr = UserTeamResponse(
    user_id="123",
    user_name="Test User",
    team_name="Dream Team",
    budget_remaining=2.5,
    starters=[rp],
    bench=[rp2],
    captain=rp,
    sub_captain=rp2,
)
assert utr.captain is not None
assert utr.captain.role == PlayerRole.CAPTAIN
print("PASS UserTeamResponse:", utr.team_name)

# --- TournamentType StrEnum ---
assert TournamentType.ISRAELI_LEAGUE == "israel"
assert TournamentType.CHAMPIONS_LEAGUE == "champions"
print("PASS TournamentType StrEnum comparison")

print()
print("All smoke tests PASSED!")

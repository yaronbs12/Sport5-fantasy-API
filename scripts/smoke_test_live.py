"""
Live smoke test against upstream Sport5 servers (Read-Only).

Validates:
- Season discovery via /api/Leagues/GetLeagues
- Player pool retrieval and Pydantic validation via /api/Players/GetTeamsAndPlayers
- Fixture metadata retrieval via /api/Leagues/Get
"""

from __future__ import annotations

import asyncio
import sys

from sport5_fantasy_api.connectors import (
    ChampionsLeagueConnector,
    EuroConnector,
    EuroleagueConnector,
    IsraeliLeagueConnector,
    WorldCupConnector,
)


async def run_smoke_test() -> None:
    """Execute live read-only verification across all supported Sport5 connectors."""
    # Ensure stdout handles UTF-8 characters cleanly on Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("Starting Live Sport5 Upstream Smoke Test (All 5 Competitions)")
    print("=" * 60)

    connectors = [
        ("Israeli Premier League (Football)", IsraeliLeagueConnector(), True),
        ("UEFA Champions League (Football)", ChampionsLeagueConnector(), True),
        ("Euroleague (Basketball)", EuroleagueConnector(), True),
        ("FIFA World Cup (Football)", WorldCupConnector(), True),
        ("UEFA Euro Archive (Football - Inactive)", EuroConnector(), False),
    ]

    for name, connector, expect_active in connectors:
        print(f"\n[Testing: {name}]")
        try:
            # 1. Season Discovery
            print("  1. Discovering active season ID...")
            season_id = await connector.discover_season_id()
            print(f"     -> Discovered seasonId: {season_id}")
            assert season_id > 0, "season_id must be positive"

            # 2. Players Pool
            print("  2. Fetching player pool (get_all_players)...")
            players = await connector.get_all_players()
            print(f"     -> Retrieved {len(players)} total players")
            if expect_active:
                assert len(players) > 0, "Player pool must not be empty for active tournament"

            # Display sample of top 3 players if available
            if players:
                print("     -> Sample players:")
                for p in players[:3]:
                    print(
                        f"        ID={p.id:<6} | Pos={p.position.value:<7} | "
                        f"Price={p.price:>4.1f}M | Active={p.is_active!s:<5} | Name={p.name}"
                    )

            # 3. Fixtures and League Metadata
            print("  3. Fetching league fixtures & rounds (get_fixtures)...")
            fixtures_meta = await connector.get_fixtures()
            print(
                f"     -> Rounds: {len(fixtures_meta.rounds)} | "
                f"Games: {len(fixtures_meta.games)} | "
                f"Deadline: {fixtures_meta.exchange_deadline}"
            )

            print(f"  [OK] {name} passed smoke validations.")

        except Exception as exc:
            print(f"  [FAIL] Error during smoke test for {name}: {exc}")
            raise
        finally:
            await connector.aclose()

    print("\n" + "=" * 60)
    print("Live Smoke Test Completed Successfully! All Upstream Connectors OK.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_smoke_test())

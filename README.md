# Sport5 Fantasy API & SDK 🏆

[![CI](https://github.com/yaronbs12/sport5-fantasy/actions/workflows/ci.yml/badge.svg)](https://github.com/yaronbs12/sport5-fantasy/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Pydantic V2](https://img.shields.io/badge/Pydantic-V2-e92063.svg)](https://docs.pydantic.dev/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-158%20passed-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-92%25-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An open-source, production-grade **asynchronous REST API and Python SDK client** for the
[Sport5 Fantasy](https://dreamteam.sport5.co.il) ecosystem. Supports Israeli Premier League,
UEFA Champions League, Euroleague Basketball, FIFA World Cup, and UEFA Euro tournaments.

---

## ⚡ Key Features

- **🚀 Asynchronous REST API & Python SDK** — Built on FastAPI and HTTPX with 100% non-blocking async execution.
- **🔌 Multi-Tournament Connector Pattern** — Decoupled strategy pattern supporting 5 Sport5 fantasy competitions:
  - 🇮🇱 `israel` — Israeli Premier League (ליגת העל / ליגת החלומות)
  - 🏆 `champions` — UEFA Champions League Fantasy
  - 🏀 `euroleague` — Euroleague Basketball Fantasy (sport-specific rules & roster)
  - 🌍 `world-cup` — FIFA World Cup Tournament Fantasy
  - 🇪🇺 `euro` — UEFA Euro Tournament (historical archive)
- **🛡️ Resilient Networking with Exponential Backoff** — Production-ready retry loop on transient failures (`502`, `503`, `504`, timeouts, connection drops) with randomized jitter (`0.05s-0.15s`) and a 2.5s cap. Strict fail-fast on auth/client errors and WAF blocks.
- **🧠 Concurrency-Safe TTL Caching** — In-memory caching guarded by `asyncio.Lock` to prevent cache stampedes while eliminating redundant upstream calls (players: 10m, fixtures: 15m, teams: 1h, season: 24h). User data is never cached.
- **🧹 Pydantic V2 Data Normalization** — Strict boundary validation, sub-unit price scaling (`5_000_000` → `5.0M`), Hebrew backtick sanitization (`Daniel`Tenenbaum` → `Daniel'Tenenbaum`), and dynamic `is_active` resolution (`isDeleted` / `isRemoved`).
- **🔐 Dual-Mode Authentication** — Supports both `Authorization: Bearer <token>` and `X-Sport5-Session: <token>` headers with direct login token exchange (`/auth/login`).
- **📦 Fully Typed (PEP 561)** — Includes `py.typed` marker for static typing and IDE autocompletion.

---

## 🏗️ Architecture & Directory Layout

```text
sport5-fantasy/
├── pyproject.toml                 # Packaging, dependencies, scripts, and tool configs
├── LICENSE                        # MIT open-source license
├── README.md                      # Documentation
├── scripts/
│   └── smoke_test_live.py         # Live upstream read-only verification script
├── sport5_fantasy_api/
│   ├── __init__.py                # Public SDK imports & package entry point
│   ├── cli.py                     # CLI entry point (`sport5-api` command)
│   ├── py.typed                   # PEP 561 typing marker
│   ├── api/
│   │   ├── dependencies.py        # Connector resolver & session token dependency
│   │   ├── main.py                # FastAPI factory, exception handlers, OpenAPI tags
│   │   └── routes/
│   │       ├── auth.py            # POST /{tournament}/auth/login
│   │       ├── private.py         # GET /{tournament}/me/team, /me/leagues
│   │       └── public.py          # GET /tournaments, /players, /teams, /fixtures
│   ├── connectors/
│   │   ├── base.py                # BaseSport5Connector: retry loop, _safe_get, parsing
│   │   ├── israeli_league.py      # Israeli Premier League connector (season 10)
│   │   ├── champions_league.py    # UEFA Champions League connector (season 12)
│   │   ├── euroleague.py          # Euroleague Basketball connector (season 11, sport=2)
│   │   ├── world_cup.py           # World Cup tournament connector (season 9)
│   │   ├── euro.py                # Euro tournament connector (season 3, inactive)
│   │   └── registry.py            # ConnectorRegistry singleton factory
│   ├── core/
│   │   ├── cache.py               # Async TTLCache with asyncio.Lock concurrency protection
│   │   ├── config.py              # Pydantic-settings with FANTASY_ prefix
│   │   └── exceptions.py          # Domain exceptions (Sport5AuthError, UpstreamError, etc.)
│   └── models/
│       ├── auth.py                # LoginRequest & TokenResponse
│       ├── enums.py               # TournamentType, Position (GK, DEF, MID, FWD), PlayerRole
│       ├── fixture.py             # Team, RoundInfo, Match, LeagueMetaResponse
│       ├── league.py              # LeagueSummary, LeagueMember
│       ├── player.py              # Player model with price & active normalization
│       └── user.py                # RosterPlayer, UserTeamResponse
└── tests/
    ├── test_api_auth_and_private.py   # Auth endpoints, Bearer token, session headers
    ├── test_api_public_routes.py      # All public routes, filters, OpenAPI metadata
    ├── test_cache_resilience.py       # TTL expiry, concurrency locks, user data isolation
    ├── test_cli.py                    # CLI argument parsing and uvicorn invocation
    ├── test_connector_resilience.py   # Exponential backoff, jitter, fail-fast behavior
    ├── test_edge_cases_models.py      # Pydantic boundary checks, date parsers, active flags
    ├── test_players_pipeline.py       # Multi-shape player payload parser & filters
    ├── test_tournament_expansion.py   # Euroleague, World Cup, Euro connectors
    └── test_waf_and_upstream_error.py # WAF HTML blocks, HTTP 502/504 translations
```

---

## 🚀 Getting Started & Installation

### Option 1: Standard Installation

```bash
# Install directly via pip
pip install .
```

### Option 2: Editable Installation (Development)

```bash
git clone https://github.com/yaronbs12/sport5-fantasy.git
cd sport5-fantasy

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
# source .venv/bin/activate   # Linux / macOS

# Install package with development dependencies
pip install -e ".[dev]"
```

### Option 3: Docker Containerization (Production)

```bash
# Build production Docker image
docker build -t sport5-fantasy:latest .

# Run container with environment configuration
docker run -d --name sport5-api -p 8000:8000 --env-file .env.example sport5-fantasy:latest
```

---

## 🖥️ Running the API Server via CLI

The package registers the executable `sport5-api` command:

```bash
# Start development server with auto-reload
sport5-api --host 0.0.0.0 --port 8000 --reload
```

### CLI Arguments

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Host interface to bind server to |
| `--port` | `8000` | Port to listen on |
| `--reload` | `false` | Enable live reload on code changes |

Once running, interactive documentation is immediately accessible at:
- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI 3.1 JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 💡 Code Examples

### Example 1: Direct Python SDK Client

Interact with Sport5 upstream directly without running the HTTP server:

```python
import asyncio
from sport5_fantasy_api import IsraeliLeagueConnector, Position

async def main() -> None:
    # Context manager automatically handles client teardown
    async with IsraeliLeagueConnector() as connector:
        # 1. Discover current active season ID
        season_id = await connector.discover_season_id()
        print(f"Current Season ID: {season_id}")

        # 2. Fetch all fantasy players (cached for 10 minutes)
        players = await connector.get_all_players()
        print(f"Total players in pool: {len(players)}")

        # 3. Filter active forwards priced <= 9.0M
        budget_fwds = [
            p for p in players
            if p.is_active and p.position == Position.FWD and p.price <= 9.0
        ]
        for p in budget_fwds[:5]:
            print(f"[{p.team_name}] {p.name} - {p.price}M (Points: {p.total_points})")

        # 4. Fetch upcoming round fixtures and deadline
        fixtures = await connector.get_fixtures()
        print(f"Next Gameweek Deadline: {fixtures.exchange_deadline}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

### Example 2: Consuming the REST API

Query public endpoints with price, position, team, and active filters:

#### Using `curl`:
```bash
# Query active midfielders in Israeli League under 8.5M
curl -X GET "http://localhost:8000/api/v1/israel/players?position=MID&max_price=8.5&include_inactive=false"
```

#### Using Python `httpx`:
```python
import httpx

with httpx.Client(base_url="http://localhost:8000/api/v1") as client:
    # Get tournament directory
    tournaments = client.get("/tournaments").json()
    print("Available Tournaments:", [t["name"] for t in tournaments])

    # Fetch club teams with logo URLs
    teams = client.get("/israel/teams").json()
    for team in teams[:3]:
        print(f"Team: {team['name']} (Logo: {team['teamLogoPath']})")
```

---

### Example 3: Authenticated Session & Squad Management

Authenticate with Sport5 credentials to manage user squads and private leagues:

```python
import httpx

BASE_URL = "http://localhost:8000/api/v1/israel"

with httpx.Client() as client:
    # 1. Login to Sport5 and acquire .AspNetCore.Cookies session token
    login_resp = client.post(
        f"{BASE_URL}/auth/login",
        json={
            "email": "user@example.com",
            "password": "my_secret_password"
        }
    )
    token_data = login_resp.json()
    token = token_data["access_token"]
    print("Acquired Session Token successfully.")

    # 2. Query private user team using Bearer token
    headers = {"Authorization": f"Bearer {token}"}
    squad_resp = client.get(f"{BASE_URL}/me/team", headers=headers)
    squad = squad_resp.json()

    print(f"User: {squad['user_name']} | Team: {squad['team_name']}")
    print(f"Budget Remaining: {squad['budget_remaining']}M")
    print(f"Captain: {squad['captain']['name'] if squad['captain'] else 'None'}")
    print(f"Starters Count: {len(squad['starters'])} (Bench: {len(squad['bench'])})")

    # 3. Query user's private mini-leagues
    leagues_resp = client.get(f"{BASE_URL}/me/leagues", headers=headers)
    for league in leagues_resp.json():
        print(f"League: {league['name']} | Members: {league['member_count']}")
```

---

## ⚙️ Environment Configuration

All settings can be customized via `.env` or system environment variables:

| Variable | Default | Description |
|---|---|---|
| `FANTASY_DEBUG` | `false` | Enable FastAPI debug mode |
| `FANTASY_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |
| `FANTASY_HTTP_TIMEOUT_SECONDS` | `15.0` | Timeout in seconds for upstream HTTP requests |
| `FANTASY_HTTP_MAX_RETRIES` | `3` | Maximum automatic retries on transient errors |
| `FANTASY_CACHE_TTL_PLAYERS` | `600` | Player pool cache TTL in seconds (10m) |
| `FANTASY_CACHE_TTL_FIXTURES` | `900` | Fixture metadata cache TTL in seconds (15m) |
| `FANTASY_CACHE_TTL_TEAMS` | `3600` | Team directory cache TTL in seconds (1h) |
| `FANTASY_CACHE_TTL_SEASON_DISCOVERY` | `86400` | Season discovery cache TTL in seconds (24h) |

---

## 🧪 Testing & Quality Assurance

The project maintains 100% passing tests with rigorous edge-case coverage:

```bash
# Run complete test suite with coverage report
pytest

# Target specific resilience test suite
pytest tests/test_connector_resilience.py -v

# Run linter and code style enforcement
ruff check .

# Format code
ruff format .

# Run live smoke test against upstream Sport5 servers
python scripts/smoke_test_live.py
```

---

## 🛡️ Upstream Error Handling Matrix

| HTTP Status | Domain Exception | Upstream Trigger |
|---|---|---|
| `401 Unauthorized` | `Sport5AuthError` | Expired / invalid `.AspNetCore.Cookies` session token |
| `404 Not Found` | `HTTPException` | Unknown player ID or unsupported tournament slug |
| `502 Bad Gateway` | `Sport5WAFBlockError` | Upstream returned DataDome HTML challenge block |
| `502 Bad Gateway` | `Sport5UpstreamError` | Upstream returned HTTP 502/503/504 (after retries) |
| `502 Bad Gateway` | `Sport5DataError` | Upstream JSON structure payload changed unexpectedly |
| `500 Server Error` | `Exception` | Unhandled internal error |

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## ⚠️ Disclaimer

This project is an unofficial, reverse-engineered client for the Sport5 Fantasy platform.
It is not affiliated with, endorsed by, or officially supported by Sport5 (ערוץ הספורט),
RGE Media Group, or Keshet. Use responsibly and in accordance with upstream terms of service.

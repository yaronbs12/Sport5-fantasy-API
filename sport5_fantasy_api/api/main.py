"""
FastAPI application factory for the Sport5 Fantasy API.

Application Configuration
--------------------------
- Title:     Sport5 Fantasy API
- Version:   0.1.0
- Docs:      /docs  (Swagger UI)
- ReDoc:     /redoc
- OpenAPI:   /openapi.json

Middleware
----------
- **CORSMiddleware**: Wildcard origins by default (configurable via
  ``FANTASY_CORS_ORIGINS`` environment variable). Suitable for local
  bot/client development; restrict to specific origins in production.

Global Exception Handlers
--------------------------
- ``Sport5AuthError``        → HTTP 401 Unauthorized
- ``Sport5WAFBlockError``    → HTTP 502 Bad Gateway
- ``Sport5UpstreamError``   → HTTP 502 Bad Gateway
- ``Sport5DataError``        → HTTP 502 Bad Gateway
- Unhandled ``Exception``    → HTTP 500 Internal Server Error (with logging)

Lifecycle
---------
- **Startup**: Logs the active configuration and warms up the registry.
- **Shutdown**: Gracefully closes all connector HTTP clients to avoid
  resource leaks.

Run via uvicorn::

    uvicorn sport5_fantasy_api.api.main:app --reload
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sport5_fantasy_api.api.routes.auth import router as auth_router
from sport5_fantasy_api.api.routes.private import router as private_router
from sport5_fantasy_api.api.routes.public import router as public_router
from sport5_fantasy_api.connectors.registry import registry
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5DataError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager for startup and shutdown hooks."""
    # --- Startup ---
    logger.info(
        "Starting %s v%s | debug=%s",
        settings.app_title,
        settings.app_version,
        settings.debug,
    )
    logger.info(
        "Israeli League URL: %s (default seasonId=%d)",
        settings.israeli_league_base_url,
        settings.israeli_league_default_season_id,
    )
    logger.info(
        "Champions League URL: %s (default seasonId=%d)",
        settings.champions_league_base_url,
        settings.champions_league_default_season_id,
    )
    logger.info(
        "Euroleague URL: %s (default seasonId=%d, sport=Basketball)",
        settings.euroleague_base_url,
        settings.euroleague_default_season_id,
    )
    logger.info(
        "World Cup URL: %s (default seasonId=%d)",
        settings.world_cup_base_url,
        settings.world_cup_default_season_id,
    )
    logger.info(
        "Euro URL: %s (default seasonId=%d, active=False)",
        settings.euro_base_url,
        settings.euro_default_season_id,
    )

    yield

    # --- Shutdown ---
    logger.info("Shutting down — closing connector HTTP clients.")
    await registry.close_all()
    logger.info("All connectors closed. Goodbye.")


# ---------------------------------------------------------------------------
# OpenAPI tags and description metadata
# ---------------------------------------------------------------------------

_OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "Tournaments",
        "description": (
            "Tournament discovery and directory metadata. Lists all 5 supported "
            "competitions (`israel`, `champions`, `euroleague`, `world-cup`, `euro`), "
            "including sport types (Football / Basketball), current season IDs, and active status."
        ),
    },
    {
        "name": "Public",
        "description": (
            "Public fantasy data operations: browse player pool, club teams, and match fixtures. "
            "Supports 5 tournaments: `israel` (Israeli Premier League), "
            "`champions` (UEFA Champions League), `euroleague` (Euroleague Basketball), "
            "`world-cup` (FIFA World Cup), and `euro` (UEFA Euro — inactive archive tournament)."
        ),
    },
    {
        "name": "Auth",
        "description": (
            "Authentication endpoints. Exchange Sport5 user credentials (email & password) "
            "for an `.AspNetCore.Cookies` session token to access private endpoints."
        ),
    },
    {
        "name": "Private (Authenticated)",
        "description": (
            "User-specific squad, lineup, and custom league endpoints requiring authentication. "
            "Pass session token via `Authorization: Bearer <token>` or `X-Sport5-Session: <token>`."
        ),
    },
    {
        "name": "Health",
        "description": "API service health check and diagnostic monitoring.",
    },
]

_API_DESCRIPTION = """
## Sport5 Fantasy API 🏆

An open-source, asynchronous REST API and Python client library for the
**Sport5 Fantasy** ecosystem, supporting both Football and Basketball fantasy tournaments.

### Supported Tournaments

| Tournament | Slug | Sport | Status | Season | Upstream Host |
|---|---|---|---|---|---|
| Israeli Premier League (ליגת העל) | `israel` | Football | Active | 10 | `dreamteam.sport5.co.il` |
| UEFA Champions League | `champions` | Football | Active | 12 | `dreamteam.sport5.co.il` |
| Euroleague (יורוליג) | `euroleague` | Basketball | Active | 11 | `dreamteam.sport5.co.il` |
| World Cup (מונדיאל) | `world-cup` | Football | Active | 9 | `dreamteam.sport5.co.il` |
| UEFA Euro (Euro Fantasy) | `euro` | Football | Inactive | 3 | `dreamteam.sport5.co.il` |

> **Tournament Notes:**
> - `euroleague` is **basketball-specific** (Euroleague Basketball fantasy platform).
> - `euro` is an **inactive archive tournament** (`active=False`) representing
>   past European Championships.

### Authentication

Private endpoints (``/me/*``) require a valid Sport5 session cookie supplied via
either:
- ``Authorization: Bearer <token>`` HTTP header (click **Authorize** in Swagger UI)
- ``X-Sport5-Session: <token>`` HTTP header

**How to obtain the session token:**
1. **Via API Login**: Call ``POST /api/v1/{tournament}/auth/login``
   with your Sport5 email and password.
2. **Via Browser DevTools**:
   - Open [dreamteam.sport5.co.il](https://dreamteam.sport5.co.il) in your browser and log in.
   - Open **DevTools → Application → Cookies → dreamteam.sport5.co.il**.
   - Copy the value of the ``.AspNetCore.Cookies`` cookie.

### Caching

| Resource | Cache TTL |
|---|---|
| Season discovery | 24 hours |
| Team mappings | 1 hour |
| Player pool | 10 minutes |
| Fixtures | 15 minutes |
| User data | Not cached |
"""


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Returns
    -------
    FastAPI
        The fully-configured application instance.
    """
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        debug=settings.debug,
    )

    # --- CORS middleware ---
    cors_allow_credentials = settings.cors_origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS", "HEAD"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # --- Exception handlers ---
    _register_exception_handlers(app)

    # --- Routers ---
    _prefix = "/api/v1"
    app.include_router(auth_router, prefix=_prefix)
    app.include_router(public_router, prefix=_prefix)
    app.include_router(private_router, prefix=_prefix)

    # --- Health check ---
    @app.get(
        "/health",
        tags=["Health"],
        summary="Health check",
        description="Returns a simple OK payload to verify the service is running.",
    )
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    return app


# ---------------------------------------------------------------------------
# Exception handler registration
# ---------------------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    """Attach global domain exception handlers to the application."""

    @app.exception_handler(Sport5AuthError)
    async def handle_auth_error(
        _request: Request,
        exc: Sport5AuthError,
    ) -> JSONResponse:
        logger.warning("Sport5AuthError: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": (
                    "Authentication failed or session expired. "
                    "Update the X-Sport5-Session header with a fresh "
                    ".AspNetCore.Cookies token."
                ),
                "detail": exc.message,
            },
        )

    @app.exception_handler(Sport5WAFBlockError)
    async def handle_waf_error(
        _request: Request,
        exc: Sport5WAFBlockError,
    ) -> JSONResponse:
        logger.error("Sport5WAFBlockError: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": (
                    "Sport5 upstream service unavailable or blocked by WAF. "
                    "Try again later or rotate your session cookie."
                ),
                "detail": exc.message,
            },
        )

    @app.exception_handler(Sport5UpstreamError)
    async def handle_upstream_error(
        _request: Request,
        exc: Sport5UpstreamError,
    ) -> JSONResponse:
        logger.error(
            "Sport5UpstreamError (HTTP %s): %s",
            exc.status_code,
            exc.message,
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": "Sport5 upstream service returned an unexpected error.",
                "detail": exc.message,
                "upstream_status": exc.status_code,
            },
        )

    @app.exception_handler(Sport5DataError)
    async def handle_data_error(
        _request: Request,
        exc: Sport5DataError,
    ) -> JSONResponse:
        logger.error("Sport5DataError: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": (
                    "Sport5 upstream returned an unexpected data structure. "
                    "The API schema may have changed."
                ),
                "detail": exc.message,
            },
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.critical(
            "Unhandled exception: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "An unexpected internal server error occurred.",
                "detail": str(exc),
            },
        )


# ---------------------------------------------------------------------------
# Module-level application instance
# ---------------------------------------------------------------------------

app: FastAPI = create_app()

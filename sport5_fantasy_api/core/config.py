"""
Application configuration powered by Pydantic-Settings.

Settings are loaded in priority order:
  1. Environment variables (highest priority).
  2. `.env` file in the working directory.
  3. Hardcoded defaults in the `Settings` class.

All tuneable values are exposed via the singleton ``settings`` object
imported throughout the application.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Global application settings.

    Every field can be overridden by an environment variable with the same
    name (case-insensitive) or the ``FANTASY_`` prefixed variant when
    ``env_prefix`` is set.
    """

    model_config = SettingsConfigDict(
        env_prefix="FANTASY_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Allow extra env vars without raising a ValidationError so that
        # container environments with many injected variables stay clean.
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Server settings
    # ------------------------------------------------------------------

    app_title: str = Field(
        default="Sport5 Fantasy API",
        description="FastAPI application title shown in OpenAPI docs.",
    )
    app_version: str = Field(
        default="0.1.0",
        description="Semantic version string for the API.",
    )
    debug: bool = Field(
        default=False,
        description="Enable FastAPI debug mode (never use True in production).",
    )

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    cors_origins: list[str] = Field(
        default=["*"],
        description=(
            "List of allowed CORS origins. Defaults to wildcard for local "
            "bot/client development. Restrict in production."
        ),
    )

    # ------------------------------------------------------------------
    # Israeli Premier League connector defaults
    # ------------------------------------------------------------------

    israeli_league_base_url: str = Field(
        default="https://dreamteam.sport5.co.il",
        description="Base URL for the Israeli Dream Team (Premier League) API.",
    )
    israeli_league_default_season_id: int = Field(
        default=10,
        ge=1,
        description="Fallback seasonId for the Israeli Premier League.",
    )

    # ------------------------------------------------------------------
    # UEFA Champions League connector defaults
    # ------------------------------------------------------------------

    champions_league_base_url: str = Field(
        default="https://dreamteam.sport5.co.il",
        description="Base URL for the UEFA Champions League Fantasy API.",
    )
    champions_league_default_season_id: int = Field(
        default=12,
        ge=1,
        description="Fallback seasonId for the UEFA Champions League.",
    )

    # ------------------------------------------------------------------
    # Euroleague Basketball connector defaults
    # ------------------------------------------------------------------

    euroleague_base_url: str = Field(
        default="https://dreamteam.sport5.co.il",
        description="Base URL for the Euroleague Basketball Fantasy API.",
    )
    euroleague_default_season_id: int = Field(
        default=11,
        ge=1,
        description="Fallback seasonId for the Euroleague Basketball competition.",
    )

    # ------------------------------------------------------------------
    # World Cup connector defaults
    # ------------------------------------------------------------------

    world_cup_base_url: str = Field(
        default="https://dreamteam.sport5.co.il",
        description="Base URL for the World Cup Fantasy API.",
    )
    world_cup_default_season_id: int = Field(
        default=9,
        ge=1,
        description="Fallback seasonId for the World Cup competition.",
    )

    # ------------------------------------------------------------------
    # Euro connector defaults
    # ------------------------------------------------------------------

    euro_base_url: str = Field(
        default="https://dreamteam.sport5.co.il",
        description="Base URL for the Euro Fantasy API.",
    )
    euro_default_season_id: int = Field(
        default=3,
        ge=1,
        description="Fallback seasonId for the Euro competition.",
    )

    # ------------------------------------------------------------------
    # Cache TTLs (seconds)
    # ------------------------------------------------------------------

    cache_ttl_season_discovery: int = Field(
        default=86_400,  # 24 hours
        ge=60,
        description="TTL in seconds for cached season/league discovery responses.",
    )
    cache_ttl_teams: int = Field(
        default=3_600,  # 1 hour
        ge=60,
        description="TTL in seconds for cached team mappings.",
    )
    cache_ttl_players: int = Field(
        default=600,  # 10 minutes
        ge=30,
        description="TTL in seconds for the full cached player pool.",
    )
    cache_ttl_fixtures: int = Field(
        default=900,  # 15 minutes
        ge=30,
        description="TTL in seconds for cached fixture / round data.",
    )

    # ------------------------------------------------------------------
    # HTTP client settings
    # ------------------------------------------------------------------

    http_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="Total timeout (seconds) for outbound upstream HTTP requests.",
    )
    http_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum number of automatic retries on transient network errors.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_string(cls, v: object) -> object:
        """Allow CORS origins to be supplied as a comma-separated string."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere instead of instantiating
# Settings() repeatedly.
# ---------------------------------------------------------------------------

settings: Settings = Settings()

"""
Abstract base connector for the Sport5 fantasy API ecosystem.

Architecture Overview
---------------------
``BaseSport5Connector`` is the core domain coordinator that:
  1. Manages tournament identity, fallback season IDs, and TTL caching.
  2. Delegates resilient HTTP networking and WAF mitigation to ``Sport5HttpClient``.
  3. Delegates payload normalization to modular parsers in ``parsers/``.
  4. Exposes high-level asynchronous domain methods:
     - ``discover_season_id()``
     - ``get_league_details()``
     - ``get_teams()``, ``get_teams_mapping()``
     - ``get_all_players()``, ``get_player_by_id()``
     - ``get_fixtures()``
     - ``login()``
     - ``get_user_team()``
     - ``get_user_leagues()``
     - ``get_league_leaderboard()``

Subclasses declare:
  - ``base_url``
  - ``default_season_id``
  - ``tournament_type``
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from sport5_fantasy_api.connectors.http_client import (
    _CHROME_HEADERS,
    _HTML_INDICATORS,
    CHROME_HEADERS,
    HTML_INDICATORS,
    Sport5HttpClient,
)
from sport5_fantasy_api.connectors.parsers import (
    extract_raw_players,
    parse_fixtures_payload,
    parse_leaderboard_payload,
    parse_players_list,
    parse_user_leagues_payload,
    parse_user_team_payload,
)
from sport5_fantasy_api.connectors.retry import (
    _FAIL_FAST_STATUS_CODES,
    _RETRY_BACKOFF_FACTOR,
    _RETRY_BASE_DELAY,
    _RETRY_MAX_ATTEMPTS,
    _RETRY_MAX_DELAY,
    _RETRY_STATUS_CODES,
    _TRANSIENT_EXCEPTIONS,
    FAIL_FAST_STATUS_CODES,
    RETRY_BACKOFF_FACTOR,
    RETRY_BASE_DELAY,
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_DELAY,
    RETRY_STATUS_CODES,
    TRANSIENT_EXCEPTIONS,
    _calculate_backoff_delay,
    calculate_backoff_delay,
)
from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5UpstreamError,
)
from sport5_fantasy_api.models.enums import TournamentType
from sport5_fantasy_api.models.fixture import LeagueMetaResponse, Team
from sport5_fantasy_api.models.league import LeagueLeaderboard, LeagueSummary
from sport5_fantasy_api.models.player import Player
from sport5_fantasy_api.models.user import UserTeamResponse

logger = logging.getLogger(__name__)

# Re-export retry and HTTP symbols for backwards compatibility with tests and callers
__all__ = [
    "CHROME_HEADERS",
    "FAIL_FAST_STATUS_CODES",
    "HTML_INDICATORS",
    "RETRY_BACKOFF_FACTOR",
    "RETRY_BASE_DELAY",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_MAX_DELAY",
    "RETRY_STATUS_CODES",
    "TRANSIENT_EXCEPTIONS",
    "_CHROME_HEADERS",
    "_FAIL_FAST_STATUS_CODES",
    "_HTML_INDICATORS",
    "_RETRY_BACKOFF_FACTOR",
    "_RETRY_BASE_DELAY",
    "_RETRY_MAX_ATTEMPTS",
    "_RETRY_MAX_DELAY",
    "_RETRY_STATUS_CODES",
    "_TRANSIENT_EXCEPTIONS",
    "BaseSport5Connector",
    "Sport5HttpClient",
    "_calculate_backoff_delay",
    "calculate_backoff_delay",
]


class BaseSport5Connector(ABC):
    """
    Abstract base class for Sport5 fantasy API connectors.

    Parameters
    ----------
    cache:
        Optional external ``TTLCache`` instance to share across connectors.
        If ``None``, a fresh private cache is created.
    """

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL for this connector's upstream API."""
        ...

    @property
    @abstractmethod
    def default_season_id(self) -> int:
        """Fallback seasonId when discovery fails."""
        ...

    @property
    @abstractmethod
    def tournament_type(self) -> TournamentType:
        """Tournament type handled by this connector."""
        ...

    def __init__(self, cache: TTLCache | None = None) -> None:
        self.cache: TTLCache = cache or TTLCache()
        self._http_client = Sport5HttpClient(
            base_url=self.base_url,
            timeout_seconds=settings.http_timeout_seconds,
            tournament_tag=str(self.tournament_type),
        )

    # ------------------------------------------------------------------
    # HTTP Client delegation & backwards compatibility hooks
    # ------------------------------------------------------------------

    @property
    def _client(self) -> httpx.AsyncClient | None:
        """Shared client access preserving legacy private property."""
        return self._http_client._client

    @_client.setter
    def _client(self, value: httpx.AsyncClient | None) -> None:
        self._http_client._client = value

    def _build_client(self) -> httpx.AsyncClient:
        return self._http_client.build_client()

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._http_client.get_client()

    async def aclose(self) -> None:
        """Close underlying HTTP client connections."""
        await self._http_client.aclose()

    async def __aenter__(self) -> BaseSport5Connector:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def _safe_get(
        self,
        path: str,
        *,
        auth_cookie: str | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = RETRY_MAX_ATTEMPTS,
        base_delay: float = RETRY_BASE_DELAY,
        backoff_factor: float = RETRY_BACKOFF_FACTOR,
        max_delay: float = RETRY_MAX_DELAY,
    ) -> Any:
        client = await self._get_client()
        return await self._http_client.safe_get(
            path,
            client=client,
            auth_cookie=auth_cookie,
            params=params,
            max_retries=max_retries,
            base_delay=base_delay,
            backoff_factor=backoff_factor,
            max_delay=max_delay,
        )

    async def _safe_post(
        self,
        path: str,
        *,
        auth_cookie: str | None = None,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        max_retries: int = RETRY_MAX_ATTEMPTS,
        base_delay: float = RETRY_BASE_DELAY,
        backoff_factor: float = RETRY_BACKOFF_FACTOR,
        max_delay: float = RETRY_MAX_DELAY,
    ) -> Any:
        client = await self._get_client()
        return await self._http_client.safe_post(
            path,
            client=client,
            auth_cookie=auth_cookie,
            json_data=json_data,
            params=params,
            max_retries=max_retries,
            base_delay=base_delay,
            backoff_factor=backoff_factor,
            max_delay=max_delay,
        )

    # ------------------------------------------------------------------
    # Season Discovery & Metadata
    # ------------------------------------------------------------------

    async def discover_season_id(self) -> int:
        """
        Discover the current active season ID by querying ``/api/Leagues/GetLeagues``.

        Caches discovery response for 24 hours. Falls back to ``default_season_id``
        on any upstream error.
        """
        cache_key = f"season_id:{self.tournament_type}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return int(cached)

        url_name_map: dict[TournamentType, str] = {
            TournamentType.ISRAELI_LEAGUE: "dreamteam",
            TournamentType.CHAMPIONS_LEAGUE: "fantasyleague",
            TournamentType.EUROLEAGUE: "fantasyeuroleague",
            TournamentType.WORLD_CUP: "fantasywc",
            TournamentType.EURO: "eurofantasy",
        }
        target_url_name = url_name_map.get(self.tournament_type)

        try:
            data = await self._safe_get("/api/Leagues/GetLeagues")
            leagues: list[dict[str, Any]] = data.get("data", []) if isinstance(data, dict) else data

            matched: dict[str, Any] | None = None
            if target_url_name:
                matched = next(
                    (
                        lg
                        for lg in leagues
                        if lg.get("urlName", "").lower() == target_url_name.lower()
                    ),
                    None,
                )

            if matched is None:
                matched = next(
                    (lg for lg in leagues if lg.get("isActive", False)),
                    leagues[0] if leagues else None,
                )

            if matched is not None:
                season_id = int(
                    matched.get("currentSeasonId")
                    or matched.get("seasonId")
                    or self.default_season_id
                )
                await self.cache.set(
                    cache_key,
                    season_id,
                    ttl_seconds=settings.cache_ttl_season_discovery,
                )
                logger.info(
                    "[%s] Discovered seasonId=%d (urlName=%r)",
                    self.tournament_type,
                    season_id,
                    matched.get("urlName"),
                )
                return season_id
        except Exception as exc:
            logger.warning(
                "[%s] Season discovery failed (%s). Falling back to default=%d.",
                self.tournament_type,
                exc,
                self.default_season_id,
            )

        return self.default_season_id

    # ------------------------------------------------------------------
    # Public Data Methods
    # ------------------------------------------------------------------

    async def get_league_details(self) -> dict[str, Any]:
        """Fetch and cache the raw ``/api/Leagues/Get`` response ``data`` envelope."""
        season_id = await self.discover_season_id()
        cache_key = f"league_details:{self.tournament_type}:{season_id}"
        cached: dict[str, Any] | None = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        raw = await self._safe_get(
            "/api/Leagues/Get",
            params={"seasonId": season_id},
        )
        league_data: dict[str, Any] = raw.get("data", {}) if isinstance(raw, dict) else {}

        await self.cache.set(cache_key, league_data, ttl_seconds=settings.cache_ttl_teams)
        logger.info(
            "[%s] Fetched league details for seasonId=%d",
            self.tournament_type,
            season_id,
        )
        return league_data

    async def get_teams(self) -> list[Team]:
        """Fetch the full list of club teams for the current season."""
        league_data = await self.get_league_details()
        teams_raw: list[dict[str, Any]] = league_data.get("teams", [])

        teams: list[Team] = []
        for raw in teams_raw:
            try:
                teams.append(Team.model_validate(raw))
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed team record %r: %s",
                    self.tournament_type,
                    raw.get("id"),
                    exc,
                )
        return teams

    async def get_teams_mapping(self) -> dict[int, str]:
        """Fetch the {team_id: team_name} mapping for the current season."""
        teams = await self.get_teams()
        return {t.id: t.name for t in teams}

    async def get_all_players(self) -> list[Player]:
        """Fetch the complete fantasy player pool, normalized into Player domain models."""
        cache_key = f"{self.tournament_type}:players"
        cached: list[Player] | None = await self.cache.get(cache_key)
        if cached is not None:
            return list(cached)

        season_id = await self.discover_season_id()
        raw_players: list[dict[str, Any]] = []

        try:
            raw_payload = await self._safe_get(
                "/api/Players/GetTeamsAndPlayers",
                params={"seasonId": season_id},
            )
            raw_players = extract_raw_players(raw_payload)
        except Exception as exc:
            logger.warning(
                "[%s] /api/Players/GetTeamsAndPlayers failed: %s. "
                "Attempting fallback via get_league_details.",
                self.tournament_type,
                exc,
            )

        # Fallback to get_league_details() if GetTeamsAndPlayers yielded nothing
        if not raw_players:
            try:
                league_data = await self.get_league_details()
                raw_players = league_data.get("players", [])
                teams_lookup: dict[int, str] = {
                    int(t["id"]): str(t.get("name", "Unknown"))
                    for t in league_data.get("teams", [])
                    if "id" in t
                }
                for i, raw in enumerate(raw_players):
                    if isinstance(raw, dict) and "teamName" not in raw and "teamId" in raw:
                        raw_players[i] = {
                            **raw,
                            "teamName": teams_lookup.get(int(raw["teamId"]), "Unknown"),
                        }
            except Exception as exc:
                logger.warning(
                    "[%s] Fallback get_league_details also failed: %s",
                    self.tournament_type,
                    exc,
                )

        players = parse_players_list(raw_players, tournament_tag=str(self.tournament_type))

        await self.cache.set(cache_key, players, ttl_seconds=settings.cache_ttl_players)
        logger.info(
            "[%s] Fetched %d players for seasonId=%d",
            self.tournament_type,
            len(players),
            season_id,
        )
        return players

    async def get_player_by_id(self, player_id: int) -> Player | None:
        """Fetch a single player by ID from the cached pool."""
        players = await self.get_all_players()
        for p in players:
            if p.id == player_id:
                return p
        return None

    async def get_fixtures(self) -> LeagueMetaResponse:
        """Fetch full league metadata: rounds, fixtures, and transfer deadlines."""
        season_id = await self.discover_season_id()
        cache_key = f"fixtures:{self.tournament_type}:{season_id}"
        cached: LeagueMetaResponse | None = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        league_data = await self.get_league_details()
        response = parse_fixtures_payload(
            league_data,
            season_id=season_id,
            tournament_tag=str(self.tournament_type),
        )

        await self.cache.set(cache_key, response, ttl_seconds=settings.cache_ttl_fixtures)
        return response

    # ------------------------------------------------------------------
    # Authentication & User Private Methods
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        """
        Authenticate against Sport5 and extract the .AspNetCore.Cookies session token.

        Never logs password or credentials.
        """
        client = await self._get_client()
        payload = {
            "email": email,
            "password": password,
            "googleToken": None,
            "facebookToken": None,
            "adminKey": None,
        }

        response: httpx.Response | None = None
        for attempt in range(RETRY_MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    "/api/Account/Login",
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = calculate_backoff_delay(
                        attempt, RETRY_BASE_DELAY, RETRY_BACKOFF_FACTOR, RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "[%s] Transient network error during login (%s). "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        exc.__class__.__name__,
                        delay,
                        attempt + 1,
                        RETRY_MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(delay)
                    continue

                if isinstance(exc, httpx.TimeoutException):
                    raise Sport5UpstreamError(
                        f"Login request timed out after {settings.http_timeout_seconds}s."
                    ) from exc
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream during login: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream during login: {exc}"
                ) from exc

            if response.status_code in RETRY_STATUS_CODES:
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = calculate_backoff_delay(
                        attempt, RETRY_BASE_DELAY, RETRY_BACKOFF_FACTOR, RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "[%s] Upstream returned transient HTTP %d during login. "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        response.status_code,
                        delay,
                        attempt + 1,
                        RETRY_MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream during login.",
                    status_code=response.status_code,
                )

            break

        if response is None:
            raise Sport5UpstreamError("Login request failed to produce a response.")

        if response.status_code != 200:
            logger.warning(
                "[%s] Sport5 login rejected with HTTP %d",
                self.tournament_type,
                response.status_code,
            )
            raise Sport5AuthError(
                f"Invalid credentials or login blocked "
                f"(upstream returned HTTP {response.status_code})."
            )

        text_preview = response.text[:200].lstrip()
        if any(text_preview.startswith(indicator) for indicator in HTML_INDICATORS):
            logger.error(
                "[%s] Sport5 returned HTML body during login (WAF block or login wall).",
                self.tournament_type,
            )
            raise Sport5AuthError("Invalid credentials or login blocked (WAF/HTML response).")

        token: str | None = None
        try:
            token = response.cookies.get(".AspNetCore.Cookies")
        except Exception:
            token = None

        if not token:
            for raw_cookie in response.headers.get_list("set-cookie"):
                if ".AspNetCore.Cookies=" in raw_cookie:
                    cookie_part = raw_cookie.split(".AspNetCore.Cookies=")[1]
                    token = cookie_part.split(";")[0].strip()
                    if token:
                        break

        try:
            data = response.json()
            if isinstance(data, dict) and (
                data.get("succeeded") is False
                or data.get("isSuccess") is False
                or data.get("success") is False
            ):
                error_msg = (
                    data.get("error")
                    or data.get("message")
                    or "Invalid credentials or login blocked"
                )
                raise Sport5AuthError(str(error_msg))
        except (ValueError, json.JSONDecodeError):
            pass

        if not token:
            logger.warning(
                "[%s] Sport5 login succeeded with HTTP 200 but .AspNetCore.Cookies was not set.",
                self.tournament_type,
            )
            raise Sport5AuthError("Invalid credentials or login blocked: missing session cookie.")

        # Mask email when logging to avoid PII exposure
        masked_email = (
            email.split("@")[0][:2] + "***@" + email.split("@")[-1] if "@" in email else "***"
        )
        logger.info("[%s] Successfully authenticated user %s", self.tournament_type, masked_email)
        return token

    async def get_user_team(
        self,
        auth_cookie: str,
        user_id: str | None = None,
    ) -> UserTeamResponse:
        """Fetch the authenticated user's squad / lineup (never cached)."""
        season_id = await self.discover_season_id()
        params: dict[str, Any] = {"seasonId": season_id}
        if user_id is not None:
            params["userId"] = user_id

        data = await self._safe_get(
            "/api/UserTeam/GetUserAndTeam",
            auth_cookie=auth_cookie,
            params=params,
        )
        return parse_user_team_payload(data, tournament_tag=str(self.tournament_type))

    async def get_user_leagues(self, auth_cookie: str) -> list[LeagueSummary]:
        """Fetch the list of custom leagues the authenticated user belongs to (never cached)."""
        season_id = await self.discover_season_id()
        data = await self._safe_get(
            "/api/CustomLeagues/GetLeaguesSummary",
            auth_cookie=auth_cookie,
            params={"seasonId": season_id},
        )
        return parse_user_leagues_payload(data, tournament_tag=str(self.tournament_type))

    async def get_league_leaderboard(
        self,
        auth_cookie: str,
        league_id: int,
        page_index: int = 0,
    ) -> LeagueLeaderboard:
        """Fetch paginated standings and leaderboard rankings for a custom league (never cached)."""
        season_id = await self.discover_season_id()
        data = await self._safe_get(
            "/api/CustomLeagues/GetLeagueData",
            auth_cookie=auth_cookie,
            params={
                "leagueId": league_id,
                "seasonId": season_id,
                "pageIndex": page_index,
            },
        )
        return parse_leaderboard_payload(
            data,
            league_id=league_id,
            page_index=page_index,
            tournament_tag=str(self.tournament_type),
        )

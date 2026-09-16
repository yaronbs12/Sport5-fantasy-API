"""
Abstract base connector for the Sport5 fantasy API ecosystem.

Architecture Overview
---------------------
``BaseSport5Connector`` is an abstract class that:
  1. Maintains a single shared ``httpx.AsyncClient`` configured with
     production-grade Chrome desktop headers to avoid bot-detection.
  2. Provides a **safe request handler** that translates Sport5-specific
     failure modes (WAF HTML response, non-200 status, JSON decode errors)
     into clean domain exceptions.
  3. Implements a ``discover_season_id()`` method that hits
     ``/api/Leagues/GetLeagues`` and caches the result for 24 hours,
     falling back to ``default_season_id`` on any failure.
  4. Provides concrete implementations for all public/private data methods
     (``get_teams_mapping``, ``get_all_players``, ``get_user_team``,
     ``get_user_leagues``) that subclasses inherit.

Subclasses need only declare:
  - ``base_url``
  - ``default_season_id``
  - ``tournament_type``

The client is initialised lazily and torn down via ``aclose()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx

from sport5_fantasy_api.core.cache import TTLCache
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5DataError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)
from sport5_fantasy_api.models.enums import PlayerRole, TournamentType
from sport5_fantasy_api.models.fixture import (
    LeagueMetaResponse,
    Match,
    RoundInfo,
    Team,
    _parse_sport5_datetime,
)
from sport5_fantasy_api.models.league import LeagueSummary
from sport5_fantasy_api.models.player import Player
from sport5_fantasy_api.models.user import RosterPlayer, UserTeamResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Retry Configuration
# ---------------------------------------------------------------------------

_HTML_INDICATORS: tuple[str, ...] = ("<!doctype", "<html", "<!DOCTYPE", "<HTML")

_RETRY_MAX_ATTEMPTS: int = 3
_RETRY_BASE_DELAY: float = 0.5
_RETRY_BACKOFF_FACTOR: float = 2.0
_RETRY_MAX_DELAY: float = 2.5
_RETRY_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})
_FAIL_FAST_STATUS_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 422})
_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.TimeoutException,
)


def _calculate_backoff_delay(
    attempt: int,
    base_delay: float = _RETRY_BASE_DELAY,
    backoff_factor: float = _RETRY_BACKOFF_FACTOR,
    max_delay: float = _RETRY_MAX_DELAY,
) -> float:
    """
    Compute exponential backoff delay with random jitter.

    Formula: min(base_delay * (backoff_factor ** attempt) + uniform(0.05, 0.15), max_delay)
    """
    jitter = random.uniform(0.05, 0.15)
    delay = base_delay * (backoff_factor**attempt) + jitter
    return min(delay, max_delay)


_CHROME_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": (
        '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class BaseSport5Connector(ABC):
    """
    Abstract base class for Sport5 fantasy API connectors.

    Parameters
    ----------
    cache:
        Optional external ``TTLCache`` instance to share across connectors.
        If ``None``, a fresh private cache is created.
    """

    # ------------------------------------------------------------------
    # Subclass must declare these class-level attributes
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, cache: TTLCache | None = None) -> None:
        self.cache: TTLCache = cache or TTLCache()
        self._client: httpx.AsyncClient | None = None

    def _build_client(self) -> httpx.AsyncClient:
        """Construct and return a configured ``httpx.AsyncClient``."""
        headers = {
            **_CHROME_HEADERS,
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
        }
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            follow_redirects=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating it on first call."""
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BaseSport5Connector:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Safe request handler
    # ------------------------------------------------------------------

    async def _safe_get(
        self,
        path: str,
        *,
        auth_cookie: str | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = _RETRY_MAX_ATTEMPTS,
        base_delay: float = _RETRY_BASE_DELAY,
        backoff_factor: float = _RETRY_BACKOFF_FACTOR,
        max_delay: float = _RETRY_MAX_DELAY,
    ) -> Any:
        """
        Perform a GET request with exponential backoff retry on transient errors.

        Retries on transient network errors (ConnectTimeout, ReadTimeout, ConnectError)
        and transient upstream status codes (502, 503, 504) up to `max_retries` times.

        Strict Fail-Fast (no retry):
          - 401 / 403 -> Sport5AuthError
          - 4xx Client Errors -> Sport5UpstreamError
          - HTML / WAF block -> Sport5WAFBlockError (or Sport5AuthError if auth_cookie)
        """
        client = await self._get_client()

        request_headers: dict[str, str] = {}
        if auth_cookie:
            request_headers["Cookie"] = f".AspNetCore.Cookies={auth_cookie}"

        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    path,
                    params=params,
                    headers=request_headers,
                )
            except _TRANSIENT_EXCEPTIONS as exc:
                if attempt < max_retries:
                    delay = _calculate_backoff_delay(
                        attempt, base_delay, backoff_factor, max_delay
                    )
                    logger.warning(
                        "[%s] Transient network error on GET %r (%s). "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        path,
                        exc.__class__.__name__,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                if isinstance(exc, httpx.TimeoutException):
                    raise Sport5UpstreamError(
                        f"Upstream request to {path!r} timed out after "
                        f"{settings.http_timeout_seconds}s."
                    ) from exc
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream at {path!r}: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream at {path!r}: {exc}"
                ) from exc

            # Fail-fast: 401 / 403
            if response.status_code in (401, 403):
                logger.warning(
                    "[%s] Sport5 rejected GET %r with HTTP %d (no retry).",
                    self.tournament_type,
                    path,
                    response.status_code,
                )
                raise Sport5AuthError(
                    f"Sport5 rejected the request with HTTP {response.status_code}. "
                    "Verify the session cookie."
                )

            # Fail-fast: other 4xx client errors
            if 400 <= response.status_code < 500:
                logger.warning(
                    "[%s] Upstream returned client error HTTP %d for GET %r (no retry).",
                    self.tournament_type,
                    response.status_code,
                    path,
                )
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            # Retry on transient server status codes (502, 503, 504)
            if response.status_code in _RETRY_STATUS_CODES:
                if attempt < max_retries:
                    delay = _calculate_backoff_delay(
                        attempt, base_delay, backoff_factor, max_delay
                    )
                    logger.warning(
                        "[%s] Upstream returned transient HTTP %d for GET %r. "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        response.status_code,
                        path,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(
                    "[%s] Retries exhausted (%d attempts) for GET %r with HTTP %d.",
                    self.tournament_type,
                    max_retries + 1,
                    path,
                    response.status_code,
                )
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            # Any other unexpected non-200
            if response.status_code != 200:
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            # Strict Fail-Fast on HTML body (WAF block / auth wall)
            text_preview = response.text[:200].lstrip()
            if any(text_preview.startswith(indicator) for indicator in _HTML_INDICATORS):
                logger.error(
                    "[%s] Sport5 returned an HTML body for path %r — WAF block or auth wall.",
                    self.tournament_type,
                    path,
                )
                if auth_cookie:
                    raise Sport5AuthError(
                        "Sport5 returned an HTML page instead of JSON for an authenticated "
                        "endpoint. The session cookie is likely expired."
                    )
                raise Sport5WAFBlockError(
                    f"Sport5 WAF intercepted the request to {path!r} and returned HTML."
                )

            # JSON decode
            try:
                return response.json()
            except Exception as exc:
                raise Sport5DataError(
                    f"Failed to decode JSON from Sport5 response for path {path!r}: {exc}"
                ) from exc

    async def _safe_post(
        self,
        path: str,
        *,
        auth_cookie: str | None = None,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        max_retries: int = _RETRY_MAX_ATTEMPTS,
        base_delay: float = _RETRY_BASE_DELAY,
        backoff_factor: float = _RETRY_BACKOFF_FACTOR,
        max_delay: float = _RETRY_MAX_DELAY,
    ) -> Any:
        """
        Perform a POST request with exponential backoff retry on transient errors.
        """
        client = await self._get_client()

        request_headers: dict[str, str] = {}
        if auth_cookie:
            request_headers["Cookie"] = f".AspNetCore.Cookies={auth_cookie}"

        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    path,
                    params=params,
                    json=json_data,
                    headers=request_headers,
                )
            except _TRANSIENT_EXCEPTIONS as exc:
                if attempt < max_retries:
                    delay = _calculate_backoff_delay(
                        attempt, base_delay, backoff_factor, max_delay
                    )
                    logger.warning(
                        "[%s] Transient network error on POST %r (%s). "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        path,
                        exc.__class__.__name__,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                if isinstance(exc, httpx.TimeoutException):
                    raise Sport5UpstreamError(
                        f"Upstream request to {path!r} timed out after "
                        f"{settings.http_timeout_seconds}s."
                    ) from exc
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream at {path!r}: {exc}"
                ) from exc
            except httpx.RequestError as exc:
                raise Sport5UpstreamError(
                    f"Network error reaching Sport5 upstream at {path!r}: {exc}"
                ) from exc

            # Fail-fast: 401 / 403
            if response.status_code in (401, 403):
                raise Sport5AuthError(
                    f"Sport5 rejected the request with HTTP {response.status_code}."
                )

            # Fail-fast: other 4xx client errors
            if 400 <= response.status_code < 500:
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            # Retry on transient server errors (502, 503, 504)
            if response.status_code in _RETRY_STATUS_CODES:
                if attempt < max_retries:
                    delay = _calculate_backoff_delay(
                        attempt, base_delay, backoff_factor, max_delay
                    )
                    logger.warning(
                        "[%s] Upstream returned transient HTTP %d for POST %r. "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_type,
                        response.status_code,
                        path,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            if response.status_code != 200:
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            text_preview = response.text[:200].lstrip()
            if any(text_preview.startswith(indicator) for indicator in _HTML_INDICATORS):
                if auth_cookie:
                    raise Sport5AuthError(
                        "Sport5 returned an HTML page instead of JSON for an authenticated "
                        "endpoint. The session cookie is likely expired."
                    )
                raise Sport5WAFBlockError(
                    f"Sport5 WAF intercepted the request to {path!r} and returned HTML."
                )

            try:
                return response.json()
            except Exception as exc:
                raise Sport5DataError(
                    f"Failed to decode JSON from Sport5 response for path {path!r}: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Season discovery
    # ------------------------------------------------------------------

    async def discover_season_id(self) -> int:
        """
        Discover the current active season ID by querying ``/api/Leagues/GetLeagues``.

        Matches the response entry whose ``urlName`` corresponds to this
        connector's tournament type, using the verified Sport5 league directory:

        ============================  ====================  =========
        urlName                       TournamentType        seasonId
        ============================  ====================  =========
        ``"dreamteam"``               ISRAELI_LEAGUE        10
        ``"fantasyleague"``           CHAMPIONS_LEAGUE      12
        ``"fantasyeuroleague"``       EUROLEAGUE            11
        ``"fantasywc"``               WORLD_CUP             9
        ``"eurofantasy"``             EURO                  3
        ============================  ====================  =========

        The result is cached for ``FANTASY_CACHE_TTL_SEASON_DISCOVERY`` seconds
        (default 86400 — 24 hours).  Falls back to ``default_season_id`` if
        discovery fails or no matching entry is found.

        Returns
        -------
        int
            The current active season ID.
        """
        cache_key = f"season_id:{self.tournament_type}"
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return int(cached)

        # Mapping from TournamentType → upstream urlName
        _URL_NAME_MAP: dict[TournamentType, str] = {
            TournamentType.ISRAELI_LEAGUE: "dreamteam",
            TournamentType.CHAMPIONS_LEAGUE: "fantasyleague",
            TournamentType.EUROLEAGUE: "fantasyeuroleague",
            TournamentType.WORLD_CUP: "fantasywc",
            TournamentType.EURO: "eurofantasy",
        }
        target_url_name = _URL_NAME_MAP.get(self.tournament_type)

        try:
            data = await self._safe_get("/api/Leagues/GetLeagues")
            # Upstream structure: {"data": [{urlName, currentSeasonId, isActive, ...}]}
            leagues: list[dict[str, Any]] = (
                data.get("data", []) if isinstance(data, dict) else data
            )

            matched: dict[str, Any] | None = None
            if target_url_name:
                # Primary: match by urlName (exact, case-insensitive)
                matched = next(
                    (
                        lg
                        for lg in leagues
                        if lg.get("urlName", "").lower() == target_url_name.lower()
                    ),
                    None,
                )

            if matched is None:
                # Fallback: first active league (legacy behaviour)
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
    # Public data methods
    # ------------------------------------------------------------------

    async def get_league_details(self) -> dict[str, Any]:
        """
        Fetch and cache the raw ``/api/Leagues/Get`` response ``data`` envelope.

        This is the **single upstream call** that powers ``get_teams_mapping``,
        ``get_all_players``, and ``get_fixtures`` — all three parse the same
        cached payload, avoiding redundant network requests.

        Cached for ``FANTASY_CACHE_TTL_TEAMS`` seconds (default 3600 — 1 hour).

        Returns
        -------
        dict[str, Any]
            The raw ``data`` dict from the upstream JSON envelope.
        """
        season_id = await self.discover_season_id()
        cache_key = f"league_details:{self.tournament_type}:{season_id}"
        cached: dict[str, Any] | None = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        raw = await self._safe_get(
            "/api/Leagues/Get",
            params={"seasonId": season_id},
        )
        league_data: dict[str, Any] = (
            raw.get("data", {}) if isinstance(raw, dict) else {}
        )

        await self.cache.set(
            cache_key, league_data, ttl_seconds=settings.cache_ttl_teams
        )
        logger.info(
            "[%s] Fetched league details for seasonId=%d",
            self.tournament_type,
            season_id,
        )
        return league_data

    async def get_teams(self) -> list[Team]:
        """
        Fetch the full list of club teams for the current season.

        Verified upstream field names: ``id``, ``name``, ``teamLogoPath``,
        ``teamShirtPath`` (from ``data.teams``).

        Cached via ``get_league_details`` (TTL 3600s).

        Returns
        -------
        list[Team]
            All teams participating in the current season.
        """
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
        """
        Fetch the ``{team_id: team_name}`` mapping for the current season.

        Delegates to ``get_league_details()`` and extracts the ``data.teams``
        list, using the verified field names ``id`` and ``name``.

        Returns
        -------
        dict[int, str]
            Mapping of team ID → team display name.
        """
        teams = await self.get_teams()
        return {t.id: t.name for t in teams}

    async def get_all_players(self) -> list[Player]:
        """
        Fetch the complete fantasy player pool for the current season.

        Endpoint: GET /api/Players/GetTeamsAndPlayers?seasonId={season_id}

        Cached for ``FANTASY_CACHE_TTL_PLAYERS`` seconds (default 600 — 10 minutes).
        Cache key: ``"{tournament_type}:players"``.

        Returns
        -------
        list[Player]
            All players available for selection, including inactive ones.
        """
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

            data = (
                raw_payload.get("data")
                if isinstance(raw_payload, dict) and "data" in raw_payload
                else raw_payload
            )

            if isinstance(data, dict):
                # Case A: direct 'players' list
                if "players" in data and isinstance(data["players"], list):
                    raw_players = [p for p in data["players"] if isinstance(p, dict)]
                # Case B: 'teams' list containing nested 'players'
                elif "teams" in data and isinstance(data["teams"], list):
                    for team in data["teams"]:
                        if isinstance(team, dict):
                            t_id = team.get("id") or team.get("teamId")
                            t_name = team.get("name") or team.get("teamName") or "Unknown"
                            team_players = (
                                team.get("players") or team.get("teamPlayers") or []
                            )
                            if isinstance(team_players, list):
                                for p_dict in team_players:
                                    if isinstance(p_dict, dict):
                                        merged = dict(p_dict)
                                        if (
                                            "teamId" not in merged
                                            and "team_id" not in merged
                                            and t_id is not None
                                        ):
                                            merged["teamId"] = t_id
                                        if (
                                            "teamName" not in merged
                                            and "team_name" not in merged
                                        ):
                                            merged["teamName"] = t_name
                                        raw_players.append(merged)
            elif isinstance(data, list):
                # Case C: list of team dicts or list of player dicts
                for item in data:
                    if isinstance(item, dict):
                        if "players" in item or "teamPlayers" in item:
                            t_id = item.get("id") or item.get("teamId")
                            t_name = (
                                item.get("name") or item.get("teamName") or "Unknown"
                            )
                            team_players = (
                                item.get("players") or item.get("teamPlayers") or []
                            )
                            if isinstance(team_players, list):
                                for p_dict in team_players:
                                    if isinstance(p_dict, dict):
                                        merged = dict(p_dict)
                                        if (
                                            "teamId" not in merged
                                            and "team_id" not in merged
                                            and t_id is not None
                                        ):
                                            merged["teamId"] = t_id
                                        if (
                                            "teamName" not in merged
                                            and "team_name" not in merged
                                        ):
                                            merged["teamName"] = t_name
                                        raw_players.append(merged)
                        else:
                            raw_players.append(item)
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

        players: list[Player] = []
        for raw in raw_players:
            if not isinstance(raw, dict):
                continue
            try:
                player_raw = dict(raw)
                is_active = (
                    (not player_raw.get("isDeleted", False))
                    and (not player_raw.get("isRemoved", False))
                    and player_raw.get("isActive", True)
                )
                player_raw["isActive"] = is_active
                player_raw["is_active"] = is_active
                players.append(Player.model_validate(player_raw))
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed player record %r: %s",
                    self.tournament_type,
                    raw.get("playerId") or raw.get("id"),
                    exc,
                )

        await self.cache.set(
            cache_key, players, ttl_seconds=settings.cache_ttl_players
        )
        logger.info(
            "[%s] Fetched %d players for seasonId=%d",
            self.tournament_type,
            len(players),
            season_id,
        )
        return players

    async def get_player_by_id(self, player_id: int) -> Player | None:
        """
        Fetch a single player by their unique identifier using the cached player pool.

        Parameters
        ----------
        player_id: int
            The unique Sport5 player ID.

        Returns
        -------
        Player | None
            The matching player model, or None if not found in the pool.
        """
        players = await self.get_all_players()
        for p in players:
            if p.id == player_id:
                return p
        return None

    async def get_fixtures(self) -> LeagueMetaResponse:
        """
        Fetch full league metadata: rounds, games, and transfer deadline.

        Parses ``data.allRounds`` into ``RoundInfo`` objects and
        ``data.games`` into ``Match`` objects, then wraps everything in a
        ``LeagueMetaResponse``.

        Cached for ``FANTASY_CACHE_TTL_FIXTURES`` seconds (default 900 — 15 min).

        Returns
        -------
        LeagueMetaResponse
            Full season schedule including rounds, matches, and deadlines.
        """
        season_id = await self.discover_season_id()
        cache_key = f"fixtures:{self.tournament_type}:{season_id}"
        cached: LeagueMetaResponse | None = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        league_data = await self.get_league_details()

        # --- Rounds (data.allRounds) ---
        rounds: list[RoundInfo] = []
        for raw in league_data.get("allRounds", []):
            try:
                rounds.append(RoundInfo.model_validate(raw))
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed round record: %s",
                    self.tournament_type,
                    exc,
                )
        rounds.sort(key=lambda r: r.round_index)

        # --- Games (data.games) ---
        games: list[Match] = []
        for raw in league_data.get("games", []):
            try:
                games.append(Match.model_validate(raw))
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed game record %r: %s",
                    self.tournament_type,
                    raw.get("id"),
                    exc,
                )
        games.sort(key=lambda g: g.kickoff_time)

        # --- Transfer deadline (exchangeEndDate is the lock point) ---
        deadline_raw: Any = league_data.get("exchangeEndDate")
        exchange_deadline: datetime | None = None
        if deadline_raw:
            parsed = _parse_sport5_datetime(deadline_raw)
            if isinstance(parsed, datetime):
                exchange_deadline = parsed
            elif isinstance(parsed, str):
                try:
                    exchange_deadline = datetime.fromisoformat(parsed)
                except ValueError:
                    logger.warning(
                        "[%s] Could not parse exchangeEndDate: %r",
                        self.tournament_type,
                        deadline_raw,
                    )

        response = LeagueMetaResponse(
            season_id=season_id,
            season_name=str(league_data.get("seasonName", "")),
            current_round=int(league_data.get("currentRound", 0)),
            exchange_deadline=exchange_deadline,
            rounds=rounds,
            games=games,
        )

        await self.cache.set(
            cache_key, response, ttl_seconds=settings.cache_ttl_fixtures
        )
        return response

    # ------------------------------------------------------------------
    # Authentication and private data methods
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        """
        Authenticate against Sport5 and extract the .AspNetCore.Cookies session token.

        Sends POST {self.base_url}/api/Account/Login with user credentials.
        Extracts the .AspNetCore.Cookies session cookie from the response.

        Parameters
        ----------
        email: str
            User's registered Sport5 account email.
        password: str
            User's Sport5 account password.

        Returns
        -------
        str
            The raw .AspNetCore.Cookies session token value.

        Raises
        ------
        Sport5AuthError
            If credentials are invalid, login is blocked, or the session cookie
            is missing in the response.
        Sport5UpstreamError
            If network errors or unexpected upstream failures occur.
        """
        client = await self._get_client()
        payload = {
            "email": email,
            "password": password,
            "googleToken": None,
            "facebookToken": None,
            "adminKey": None,
        }

        try:
            response = await client.post(
                "/api/Account/Login",
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise Sport5UpstreamError(
                f"Login request timed out after {settings.http_timeout_seconds}s."
            ) from exc
        except httpx.RequestError as exc:
            raise Sport5UpstreamError(
                f"Network error reaching Sport5 upstream during login: {exc}"
            ) from exc

        # Check response status
        if response.status_code != 200:
            logger.warning(
                "[%s] Sport5 login failed with HTTP %d",
                self.tournament_type,
                response.status_code,
            )
            raise Sport5AuthError(
                f"Invalid credentials or login blocked "
                f"(upstream returned HTTP {response.status_code})."
            )

        # Check for HTML body (e.g. WAF block or redirect to error/login page)
        text_preview = response.text[:200].lstrip()
        if any(text_preview.startswith(indicator) for indicator in _HTML_INDICATORS):
            logger.error(
                "[%s] Sport5 returned HTML body during login (WAF block or login wall).",
                self.tournament_type,
            )
            raise Sport5AuthError("Invalid credentials or login blocked (WAF/HTML response).")

        # Inspect cookies for .AspNetCore.Cookies
        token: str | None = None
        try:
            token = response.cookies.get(".AspNetCore.Cookies")
        except Exception:
            token = None

        if not token:
            # Check Set-Cookie headers in case cookie jar did not parse it
            for raw_cookie in response.headers.get_list("set-cookie"):
                if ".AspNetCore.Cookies=" in raw_cookie:
                    cookie_part = raw_cookie.split(".AspNetCore.Cookies=")[1]
                    token = cookie_part.split(";")[0].strip()
                    if token:
                        break

        # Check JSON response for explicit failure indicator if provided
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

        logger.info("[%s] Successfully authenticated user %r", self.tournament_type, email)
        return token

    async def get_user_team(
        self,
        auth_cookie: str,
        user_id: str | None = None,
    ) -> UserTeamResponse:
        """
        Fetch the authenticated user's squad / lineup.

        This endpoint is **never** globally cached as it is specific to the
        authenticated user and changes frequently.

        Parameters
        ----------
        auth_cookie:
            The raw ``.AspNetCore.Cookies`` session token value (without the
            cookie name prefix).
        user_id:
            Optional Sport5 user ID. When ``None``, the upstream typically
            returns the squad for the owner of the provided session cookie.

        Returns
        -------
        UserTeamResponse
            Parsed squad with starters, bench, captain, and sub-captain.

        Raises
        ------
        Sport5AuthError
            If the session cookie is invalid or expired.
        Sport5DataError
            If the upstream response lacks the expected nested structure.
        """
        season_id = await self.discover_season_id()
        params: dict[str, Any] = {"seasonId": season_id}
        if user_id is not None:
            params["userId"] = user_id

        data = await self._safe_get(
            "/api/UserTeam/GetUserAndTeam",
            auth_cookie=auth_cookie,
            params=params,
        )

        # Upstream: {"data": {"userTeam": {...}}}
        top_level: dict[str, Any] = (
            data.get("data", {}) if isinstance(data, dict) else {}
        )
        user_team_raw: dict[str, Any] = top_level.get("userTeam", {})

        if not user_team_raw:
            raise Sport5DataError(
                "GetUserAndTeam response missing 'data.userTeam' field. "
                "The session cookie may be invalid or the user has no team."
            )

        uid: str | int = str(user_team_raw.get("userId", ""))
        user_name: str = str(user_team_raw.get("userName", ""))
        team_name: str = str(user_team_raw.get("teamName", ""))
        budget: float | None = user_team_raw.get("budget")

        players_raw: list[dict[str, Any]] = user_team_raw.get(
            "userTeamPlayers", []
        )

        starters: list[RosterPlayer] = []
        bench: list[RosterPlayer] = []
        captain: RosterPlayer | None = None
        sub_captain: RosterPlayer | None = None

        for raw in players_raw:
            try:
                roster_player = RosterPlayer.model_validate(raw)
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed roster player record %r: %s",
                    self.tournament_type,
                    raw.get("playerId"),
                    exc,
                )
                continue

            if roster_player.is_bench:
                bench.append(roster_player)
            else:
                starters.append(roster_player)

            if roster_player.role == PlayerRole.CAPTAIN:
                captain = roster_player
            elif roster_player.role == PlayerRole.SUB_CAPTAIN:
                sub_captain = roster_player

        return UserTeamResponse(
            user_id=uid,
            user_name=user_name,
            team_name=team_name,
            budget_remaining=float(budget) if budget is not None else None,
            starters=starters,
            bench=bench,
            captain=captain,
            sub_captain=sub_captain,
        )

    async def get_user_leagues(self, auth_cookie: str) -> list[LeagueSummary]:
        """
        Fetch the list of custom leagues the authenticated user belongs to.

        This endpoint is **never** globally cached.

        Parameters
        ----------
        auth_cookie:
            The raw ``.AspNetCore.Cookies`` session token value.

        Returns
        -------
        list[LeagueSummary]
            All leagues the user is enrolled in.

        Raises
        ------
        Sport5AuthError
            If the session cookie is invalid or expired.
        """
        season_id = await self.discover_season_id()
        data = await self._safe_get(
            "/api/CustomLeagues/GetLeaguesSummary",
            auth_cookie=auth_cookie,
            params={"seasonId": season_id},
        )

        # Upstream: {"data": [...league objects...]}  or  {"data": {"leagues": [...]}}
        raw_data: Any = data.get("data", []) if isinstance(data, dict) else data
        if isinstance(raw_data, dict):
            leagues_raw: list[dict[str, Any]] = raw_data.get("leagues", [])
        else:
            leagues_raw = list(raw_data) if raw_data else []

        summaries: list[LeagueSummary] = []
        for raw in leagues_raw:
            try:
                summaries.append(LeagueSummary.model_validate(raw))
            except Exception as exc:
                logger.warning(
                    "[%s] Skipping malformed league summary record: %s",
                    self.tournament_type,
                    exc,
                )

        return summaries

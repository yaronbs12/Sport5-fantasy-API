"""
HTTP client and networking resilience layer for Sport5 fantasy connectors.

Maintains httpx.AsyncClient lifecycle, browser-grade headers to mitigate WAF blocks,
and handles transient retries with exponential backoff and jitter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

import httpx

from sport5_fantasy_api.connectors.retry import (
    RETRY_BACKOFF_FACTOR,
    RETRY_BASE_DELAY,
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_DELAY,
    RETRY_STATUS_CODES,
    TRANSIENT_EXCEPTIONS,
    calculate_backoff_delay,
)
from sport5_fantasy_api.core.config import settings
from sport5_fantasy_api.core.exceptions import (
    Sport5AuthError,
    Sport5DataError,
    Sport5UpstreamError,
    Sport5WAFBlockError,
)

logger = logging.getLogger(__name__)

HTML_INDICATORS: Final[tuple[str, ...]] = ("<!doctype", "<html", "<!DOCTYPE", "<HTML")

CHROME_HEADERS: Final[dict[str, str]] = {
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
    "Sec-Ch-Ua": ('"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Backwards compatibility alias
_CHROME_HEADERS = CHROME_HEADERS
_HTML_INDICATORS = HTML_INDICATORS


class Sport5HttpClient:
    """
    Asynchronous HTTP client tailored for communication with Sport5 web services.

    Handles connection pooling, browser headers, transient backoff retries,
    and WAF HTML challenge interception.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float | None = None,
        tournament_tag: str = "generic",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.http_timeout_seconds
        )
        self.tournament_tag = tournament_tag
        self._client: httpx.AsyncClient | None = None

    def build_client(self) -> httpx.AsyncClient:
        """Construct a new configured httpx.AsyncClient."""
        headers = {
            **CHROME_HEADERS,
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
        }
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=True,
        )

    async def get_client(self) -> httpx.AsyncClient:
        """Return the shared client instance, lazily initializing if needed."""
        if self._client is None or self._client.is_closed:
            self._client = self.build_client()
        return self._client

    async def aclose(self) -> None:
        """Close client connections and release sockets."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def safe_get(
        self,
        path: str,
        *,
        client: httpx.AsyncClient | None = None,
        auth_cookie: str | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = RETRY_MAX_ATTEMPTS,
        base_delay: float = RETRY_BASE_DELAY,
        backoff_factor: float = RETRY_BACKOFF_FACTOR,
        max_delay: float = RETRY_MAX_DELAY,
    ) -> Any:
        """
        Perform GET request with exponential backoff retry on transient errors.

        Fail-fast on 4xx errors, expired auth cookies, and WAF blocks.
        """
        active_client = client if client is not None else await self.get_client()

        request_headers: dict[str, str] = {}
        if auth_cookie:
            request_headers["Cookie"] = f".AspNetCore.Cookies={auth_cookie}"

        for attempt in range(max_retries + 1):
            try:
                response = await active_client.get(
                    path,
                    params=params,
                    headers=request_headers,
                )
            except TRANSIENT_EXCEPTIONS as exc:
                if attempt < max_retries:
                    delay = calculate_backoff_delay(attempt, base_delay, backoff_factor, max_delay)
                    logger.warning(
                        "[%s] Transient network error on GET %r (%s). "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_tag,
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
                        f"Upstream request to {path!r} timed out after {self.timeout_seconds}s."
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
                    self.tournament_tag,
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
                    self.tournament_tag,
                    response.status_code,
                    path,
                )
                raise Sport5UpstreamError(
                    f"Unexpected HTTP {response.status_code} from Sport5 upstream.",
                    status_code=response.status_code,
                )

            # Transient server status codes (502, 503, 504)
            if response.status_code in RETRY_STATUS_CODES:
                if attempt < max_retries:
                    delay = calculate_backoff_delay(attempt, base_delay, backoff_factor, max_delay)
                    logger.warning(
                        "[%s] Upstream returned transient HTTP %d for GET %r. "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_tag,
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
                    self.tournament_tag,
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
            if any(text_preview.startswith(indicator) for indicator in HTML_INDICATORS):
                logger.error(
                    "[%s] Sport5 returned an HTML body for path %r — WAF block or auth wall.",
                    self.tournament_tag,
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

    async def safe_post(
        self,
        path: str,
        *,
        client: httpx.AsyncClient | None = None,
        auth_cookie: str | None = None,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        max_retries: int = RETRY_MAX_ATTEMPTS,
        base_delay: float = RETRY_BASE_DELAY,
        backoff_factor: float = RETRY_BACKOFF_FACTOR,
        max_delay: float = RETRY_MAX_DELAY,
    ) -> Any:
        """Perform POST request with exponential backoff retry on transient errors."""
        active_client = client if client is not None else await self.get_client()

        request_headers: dict[str, str] = {}
        if auth_cookie:
            request_headers["Cookie"] = f".AspNetCore.Cookies={auth_cookie}"

        for attempt in range(max_retries + 1):
            try:
                response = await active_client.post(
                    path,
                    params=params,
                    json=json_data,
                    headers=request_headers,
                )
            except TRANSIENT_EXCEPTIONS as exc:
                if attempt < max_retries:
                    delay = calculate_backoff_delay(attempt, base_delay, backoff_factor, max_delay)
                    logger.warning(
                        "[%s] Transient network error on POST %r (%s). "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_tag,
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
                        f"Upstream request to {path!r} timed out after {self.timeout_seconds}s."
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
            if response.status_code in RETRY_STATUS_CODES:
                if attempt < max_retries:
                    delay = calculate_backoff_delay(attempt, base_delay, backoff_factor, max_delay)
                    logger.warning(
                        "[%s] Upstream returned transient HTTP %d for POST %r. "
                        "Retrying in %.2fs (attempt %d/%d)...",
                        self.tournament_tag,
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
            if any(text_preview.startswith(indicator) for indicator in HTML_INDICATORS):
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

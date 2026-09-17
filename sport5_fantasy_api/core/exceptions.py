"""
Custom domain exceptions for the Sport5 Fantasy API.

These exceptions form a clean translation layer between raw upstream HTTP/WAF
errors and the structured HTTP responses returned by the FastAPI application.
"""

from __future__ import annotations


class Sport5FantasyAPIError(Exception):
    """Base exception for all Sport5 Fantasy API errors."""

    def __init__(self, message: str = "An unexpected error occurred.") -> None:
        super().__init__(message)
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(message={self.message!r})"


# ---------------------------------------------------------------------------
# Authentication / Session errors
# ---------------------------------------------------------------------------


class Sport5AuthError(Sport5FantasyAPIError):
    """
    Raised when the upstream Sport5 service rejects the provided session
    cookie (expired token, missing cookie, or a login-wall HTML redirect).

    Maps to HTTP 401 Unauthorized in the FastAPI application.
    """

    def __init__(
        self,
        message: str = (
            "Authentication failed or session expired. "
            "Please refresh your .AspNetCore.Cookies token."
        ),
    ) -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# WAF / upstream availability errors
# ---------------------------------------------------------------------------


class Sport5WAFBlockError(Sport5FantasyAPIError):
    """
    Raised when the upstream Sport5 WAF (Web Application Firewall) has
    blocked the request — typically indicated by an HTTP 200 response whose
    body is an HTML challenge page rather than the expected JSON payload.

    Maps to HTTP 502 Bad Gateway in the FastAPI application.
    """

    def __init__(
        self,
        message: str = (
            "Sport5 WAF blocked the request. The upstream returned HTML instead of JSON."
        ),
    ) -> None:
        super().__init__(message)


class Sport5UpstreamError(Sport5FantasyAPIError):
    """
    Raised when the upstream Sport5 service returns an unexpected HTTP status
    code (e.g., 5xx) or is otherwise unavailable.

    Maps to HTTP 502 Bad Gateway in the FastAPI application.
    """

    def __init__(
        self,
        message: str = "Sport5 upstream service returned an unexpected error.",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}(message={self.message!r}, status_code={self.status_code!r})"
        )


# ---------------------------------------------------------------------------
# Data / parsing errors
# ---------------------------------------------------------------------------


class Sport5DataError(Sport5FantasyAPIError):
    """
    Raised when the upstream JSON response is valid but its structure does
    not match our expected schema (e.g., missing required fields, unexpected
    data types, or empty required collections).
    """

    def __init__(
        self,
        message: str = "Unexpected data structure in Sport5 upstream response.",
    ) -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


class ConnectorNotFoundError(Sport5FantasyAPIError):
    """
    Raised when the ConnectorRegistry cannot locate a registered connector
    for the requested tournament type.
    """

    def __init__(self, tournament: str) -> None:
        super().__init__(
            f"No connector registered for tournament type: {tournament!r}. "
            "Verify the TournamentType value and ConnectorRegistry configuration."
        )
        self.tournament = tournament

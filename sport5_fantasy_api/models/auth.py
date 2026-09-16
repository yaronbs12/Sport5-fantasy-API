"""
Pydantic V2 models for authentication requests and responses.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sport5_fantasy_api.models.enums import TournamentType

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    """Credentials payload for authenticating against Sport5 Fantasy."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(
        ...,
        description="User's registered Sport5 account email address.",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=1,
        description="User's Sport5 account password.",
        examples=["password123"],
    )

    @field_validator("email", mode="after")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        """Validate email format without requiring optional third-party packages."""
        clean = v.strip().lower()
        if not _EMAIL_REGEX.match(clean):
            raise ValueError(f"Invalid email address format: {v!r}")
        return clean


class TokenResponse(BaseModel):
    """Authentication token response containing the extracted session token."""

    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(
        ...,
        description="Extracted .AspNetCore.Cookies session token value.",
        examples=["CfDJ8abc123..."],
    )
    token_type: str = Field(
        default="bearer",
        description="Token type, defaults to 'bearer'.",
        examples=["bearer"],
    )
    tournament: TournamentType = Field(
        ...,
        description="Tournament scope for this session.",
        examples=[TournamentType.ISRAELI_LEAGUE],
    )

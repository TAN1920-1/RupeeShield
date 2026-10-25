"""Authentication, session and notification schemas.

Two rules are encoded here rather than left to the routes:

* a password only ever travels *in* (``LoginRequest``) and is never part of a
  response model;
* the session token appears exactly once, in the response to a successful sign-in.
  ``SessionStatusOut`` deliberately omits it, so a page reload re-reads the user
  without the token being echoed back over the wire again.
"""
from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime


class LoginRequest(ApiModel):
    email: str = Field(..., min_length=3, max_length=120, examples=["analyst@rupeeshield.demo"])
    password: str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(
        default="D-WEB-UNKNOWN",
        max_length=40,
        description="Browser-local device fingerprint used for the new-device signal.",
    )
    country: str | None = Field(default=None, max_length=4, description="ISO-3166 alpha-2.")

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("country")
    @classmethod
    def _normalise_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned[:4] or None

    @field_validator("device_id")
    @classmethod
    def _normalise_device(cls, value: str) -> str:
        return value.strip()[:40] or "D-WEB-UNKNOWN"


class CustomerProfileOut(ApiModel):
    phone_masked: str = ""
    city: str = ""
    kyc_status: str = "VERIFIED"


class UserOut(ApiModel):
    """Everything the frontend may know about the signed-in user. No credentials."""

    id: str
    email: str
    full_name: str
    role: str
    is_demo: bool = False
    last_login_at: OptionalUtcDateTime = None
    profile: CustomerProfileOut = Field(default_factory=CustomerProfileOut)
    account_ids: list[str] = Field(default_factory=list)
    account_count: int = 0


class SessionOut(ApiModel):
    """Returned once, by ``POST /auth/login``."""

    token: str
    issued_at: UtcDateTime
    expires_at: UtcDateTime
    user: UserOut


class SessionStatusOut(ApiModel):
    """Returned by ``GET /auth/session`` — the user and the expiry, never the token."""

    issued_at: UtcDateTime
    expires_at: UtcDateTime
    user: UserOut


class DemoCredentialOut(ApiModel):
    """A published demo login. Configuration, not a secret."""

    role: str
    label: str
    email: str
    password: str
    description: str


class LogoutOut(ApiModel):
    message: str
    revoked: int = 1


class SessionSummaryOut(ApiModel):
    id: str
    issued_at: UtcDateTime
    expires_at: UtcDateTime
    client_host: str = ""
    user_agent: str = ""


__all__ = [
    "CustomerProfileOut",
    "DemoCredentialOut",
    "LoginRequest",
    "LogoutOut",
    "SessionOut",
    "SessionStatusOut",
    "SessionSummaryOut",
    "UserOut",
]

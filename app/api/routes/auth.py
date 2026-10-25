"""Local authentication endpoints.

The browser posts credentials once and receives an opaque token. Every later
request carries that token and is re-validated server-side, which is what makes
sign-out and expiry real rather than cosmetic.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.api.deps import IdentityDep, SessionDep, client_host, extract_token
from app.schemas.auth import (
    DemoCredentialOut,
    LoginRequest,
    LogoutOut,
    SessionOut,
    SessionStatusOut,
)
from app.services import auth
from app.utils.timeutils import iso_utc

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=SessionOut,
    summary="Sign in",
    description=(
        "Verifies the password against its PBKDF2 hash, records the attempt as a "
        "security event, and issues a server-side session. An unknown email and a "
        "wrong password return the same message, so the endpoint cannot be used to "
        "discover which accounts exist."
    ),
)
def login(payload: LoginRequest, session: SessionDep, request: Request) -> dict:
    _user, body = auth.authenticate(
        session,
        email=payload.email,
        password=payload.password,
        device_id=payload.device_id,
        country=payload.country,
        client_host=client_host(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return body


@router.get(
    "/session",
    response_model=SessionStatusOut,
    summary="Who am I",
    description="Resolves the presented token. Returns 401 when it is missing, expired or revoked.",
)
def read_session(session: SessionDep, identity: IdentityDep) -> dict:
    user, record = identity
    return {
        "user": auth.shape_user(session, user),
        "issued_at": iso_utc(record.issued_at),
        "expires_at": iso_utc(record.expires_at),
    }


@router.post(
    "/logout",
    response_model=LogoutOut,
    summary="Sign out",
    description="Revokes the presented session. Signing out twice is not an error.",
)
def logout(
    session: SessionDep,
    identity: IdentityDep,
    authorization: Annotated[str | None, Header()] = None,
    x_session_token: Annotated[str | None, Header()] = None,
) -> dict:
    user, _record = identity
    revoked = auth.logout(session, extract_token(authorization, x_session_token), user=user)
    return {"message": "Signed out.", "revoked": 1 if revoked else 0}


@router.post(
    "/logout-all",
    response_model=LogoutOut,
    summary="Sign out of every session",
    description="Revokes every live session for the signed-in user, including this one.",
)
def logout_all(session: SessionDep, identity: IdentityDep) -> dict:
    user, _record = identity
    revoked = auth.logout_everywhere(session, user)
    return {"message": f"Signed out of {revoked} session(s).", "revoked": revoked}


@router.get(
    "/demo-credentials",
    response_model=list[DemoCredentialOut],
    summary="The published demo logins",
    description=(
        "Demo access is a documentable feature, not a backdoor: these credentials live in "
        "configuration, are seeded on first start, and are listed in the README. The "
        "endpoint is empty when demo mode is disabled."
    ),
)
def demo_credentials() -> list[dict]:
    return auth.demo_credentials()


__all__ = ["router"]

"""Shared FastAPI dependencies and boundary helpers.

Four things are centralised here so no route has to remember them:

* the request-scoped database session;
* the sliding-window rate limit, applied to the whole API router;
* a ``client_host`` helper so limiter keys are read in exactly one place;
* **who is asking**: ``current_user`` resolves the session token on every request,
  and ``require_roles`` is the only place a role check is written.

Authorization is enforced here, on the server. Hiding a navigation item in the
browser is a usability decision; it is never the control.
"""
from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.models import ROLE_ANALYST, ROLE_CUSTOMER, User
from app.db.session import get_db
from app.security.access import AccessDenied
from app.security.ratelimit import rate_limiter
from app.services import auth as auth_service

SessionDep = Annotated[Session, Depends(get_db)]

#: The scheme advertised in ``WWW-Authenticate`` on a 401.
SESSION_SCHEME = "Session"


def client_host(request: Request) -> str:
    """Best-effort client key for the rate limiter.

    Behind a reverse proxy this would need the forwarded-for header; the app is
    documented as a single-process local deployment, so the socket peer is used.
    """
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    """Reject once a client exceeds the configured requests-per-minute budget."""
    allowed, retry_after = rate_limiter.check(client_host(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded ({rate_limiter.limit} requests per "
                f"{int(rate_limiter.window_seconds)}s). Retry in {retry_after:.0f}s."
            ),
            headers={"Retry-After": str(max(int(retry_after) + 1, 1))},
        )


def extract_token(authorization: str | None, x_session_token: str | None = None) -> str | None:
    """Read the opaque session token from either supported header.

    ``Authorization: Session <token>`` is the documented form; ``X-Session-Token``
    exists because an ``EventSource`` (the live monitoring feed) cannot set request
    headers. Both are read in this one function so the contract cannot drift.
    """
    if x_session_token:
        cleaned = x_session_token.strip()
        if cleaned:
            return cleaned
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() in ("session", "bearer"):
        return parts[1].strip() or None
    return authorization.strip() or None


def current_identity(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    x_session_token: Annotated[str | None, Header()] = None,
):
    """``(user, session_row)`` for a valid token, otherwise 401.

    Every expiry and revocation check happens inside the service, so this
    dependency cannot accidentally skip one.
    """
    return auth_service.resolve_session(
        session, extract_token(authorization, x_session_token)
    )


def current_user(identity=Depends(current_identity)) -> User:
    """The signed-in user. Raises 401 when there is no usable session."""
    return identity[0]


def optional_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    x_session_token: Annotated[str | None, Header()] = None,
) -> User | None:
    """The signed-in user or ``None`` — for endpoints that behave differently, not worse."""
    token = extract_token(authorization, x_session_token)
    if not token:
        return None
    try:
        return auth_service.resolve_session(session, token)[0]
    except Exception:
        return None


def require_roles(*roles: str) -> Callable[[User], User]:
    """Build a dependency that admits only the given roles.

    Unauthenticated callers get 401 from ``current_user``; authenticated callers
    holding the wrong role get 403. The two are kept distinct on purpose: one means
    "sign in", the other means "this is not yours".
    """

    def _dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in roles:
            allowed = " or ".join(roles)
            raise AccessDenied(
                f"This area is restricted to {allowed} accounts. You are signed in as "
                f"{user.role}."
            )
        return user

    return _dependency


CurrentUser = Annotated[User, Depends(current_user)]
IdentityDep = Annotated[tuple, Depends(current_identity)]
OptionalUser = Annotated[User | None, Depends(optional_user)]
CustomerUser = Annotated[User, Depends(require_roles(ROLE_CUSTOMER))]
AnalystUser = Annotated[User, Depends(require_roles(ROLE_ANALYST))]

__all__ = [
    "AnalystUser",
    "CurrentUser",
    "CustomerUser",
    "IdentityDep",
    "OptionalUser",
    "SESSION_SCHEME",
    "SessionDep",
    "client_host",
    "current_identity",
    "current_user",
    "enforce_rate_limit",
    "extract_token",
    "optional_user",
    "require_roles",
]

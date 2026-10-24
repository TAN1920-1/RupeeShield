"""Shared FastAPI dependencies and boundary helpers.

Three things are centralised here so no route has to remember them:

* the request-scoped database session;
* the sliding-window rate limit, applied to the whole API router;
* a ``client_host`` helper so limiter keys are read in exactly one place.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.security.ratelimit import rate_limiter

SessionDep = Annotated[Session, Depends(get_db)]


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


__all__ = ["SessionDep", "client_host", "enforce_rate_limit"]

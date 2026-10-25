"""Access-control signals raised by services.

These are *domain* exceptions, not HTTP ones: a service raises them and
:mod:`app.api.errors` maps them onto status codes, exactly like ``ValidationError``
and ``LookupError``. Keeping them separate from FastAPI means the CLI tools and the
seeder can call the same services without importing the web layer.
"""
from __future__ import annotations


class AuthenticationRequired(Exception):
    """No usable session: missing, malformed, expired or revoked (HTTP 401)."""


class AccessDenied(Exception):
    """Authenticated, but not permitted to touch this resource (HTTP 403)."""


class DuplicateSubmission(Exception):
    """Same idempotency key reused for a materially different request (HTTP 409)."""


__all__ = ["AccessDenied", "AuthenticationRequired", "DuplicateSubmission"]

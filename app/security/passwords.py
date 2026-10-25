"""Password hashing for the local authentication layer.

PBKDF2-HMAC-SHA256 from the standard library: no third-party dependency, a fresh
random salt per password, the work factor recorded inside the encoded value (so it
can be raised later without invalidating existing hashes), and a constant-time
comparison when verifying.

A plaintext password never leaves this module: only the encoded value is returned,
stored or logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.core.config import settings

ALGORITHM = "pbkdf2_sha256"
SALT_BYTES = 16

#: Deliberately generic: the caller must not be able to tell a wrong password from
#: an account that does not exist.
INVALID_CREDENTIALS = "Incorrect email or password."


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str, *, iterations: int | None = None) -> str:
    """Encode ``password`` as ``pbkdf2_sha256$iterations$salt$digest``."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    rounds = int(iterations or settings.password_iterations)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{ALGORITHM}${rounds}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification. Any malformed/legacy value fails closed."""
    if not password or not encoded:
        return False
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(rounds)
        )
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    """An opaque session token: 256 bits of CSPRNG output, URL-safe."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """SHA-256 of a session token.

    Only the digest is persisted, so the database cannot be used to replay a
    session. A plain hash (not a KDF) is correct here: the token already carries
    full entropy and is never user-chosen.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "ALGORITHM",
    "INVALID_CREDENTIALS",
    "hash_password",
    "new_token",
    "token_digest",
    "verify_password",
]

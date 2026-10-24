"""Identifier helpers.

IDs are generated with :mod:`secrets` (a CSPRNG) rather than :mod:`random`, so
identifiers cannot be predicted from a seed. Simulation *events* use seeded
pseudo-randomness for reproducibility, but their identifiers must not.
"""
from __future__ import annotations

import secrets

_ALPHABET = "0123456789abcdef"


def new_id(prefix: str, length: int = 12) -> str:
    """Return e.g. ``TXN-9f3a1c04b7e2``."""
    token = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}-{token}"

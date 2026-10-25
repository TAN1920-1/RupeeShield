"""Input validation helpers used at the API boundary.

Everything a client can send is either validated by a Pydantic model or by one of
these helpers. Nothing is interpolated into SQL: all queries go through
SQLAlchemy's expression language with bound parameters, so there is no string
building anywhere in the request path.
"""
from __future__ import annotations

import re
from typing import Iterable, TypeVar

from app.core.config import settings

T = TypeVar("T")

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")


class ValidationError(ValueError):
    """Raised when user-supplied input fails a boundary check (mapped to HTTP 422)."""


def validate_choice(value: str, allowed: Iterable[str], field: str) -> str:
    """Return ``value`` upper-cased and stripped when it is in ``allowed``."""
    normalised = (value or "").strip().upper()
    options = {option.upper() for option in allowed}
    if normalised not in options:
        raise ValidationError(f"{field} must be one of: {', '.join(sorted(options))}.")
    return normalised


def optional_choice(value: str | None, allowed: Iterable[str], field: str) -> str | None:
    if value is None or value == "":
        return None
    return validate_choice(value, allowed, field)


def page_params(
    limit: int | None, offset: int | None, *, default_limit: int | None = None
) -> tuple[int, int]:
    """Clamp pagination so a client cannot ask for the whole database at once.

    ``default_limit`` lets a panel that shows a short list (a notification feed, a
    security-event list) default to a sensible page rather than to the maximum.
    """
    fallback = settings.max_page_size if default_limit is None else min(
        int(default_limit), settings.max_page_size
    )
    resolved_limit = fallback if limit is None else int(limit)
    resolved_offset = 0 if offset is None else int(offset)
    if resolved_limit < 1:
        raise ValidationError("limit must be >= 1.")
    if resolved_offset < 0:
        raise ValidationError("offset must be >= 0.")
    return min(resolved_limit, settings.max_page_size), resolved_offset


def validate_identifier(value: str, field: str) -> str:
    """Validate an identifier-shaped path parameter (account / incident / transfer id)."""
    candidate = (value or "").strip()
    if not _ID_PATTERN.match(candidate):
        raise ValidationError(f"{field} must be 1-64 characters of letters, digits or -_:.")
    return candidate


def non_negative_amount(value: float | None, field: str = "min_amount") -> float | None:
    if value is None:
        return None
    if value < 0:
        raise ValidationError(f"{field} must be >= 0.")
    return float(value)


def sanitise_text(value: str, *, max_length: int, field: str) -> str:
    """Trim and bound free text. Control characters are stripped, not escaped."""
    cleaned = "".join(char for char in (value or "") if char.isprintable() or char in "\n\t")
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValidationError(f"{field} must not be empty.")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return cleaned

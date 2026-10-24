"""Time helpers.

All timestamps inside the platform are **naive UTC** (see ``app/db/models.py``).
These helpers are the only sanctioned way to obtain or convert them, so the
convention cannot drift per-module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (matches what SQLite stores)."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normalise any datetime (naive assumed UTC, or aware) to naive UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 string, tolerating a trailing ``Z``."""
    cleaned = value.strip().replace("Z", "+00:00")
    return to_naive_utc(datetime.fromisoformat(cleaned))


def minutes_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 60.0


def add_minutes(value: datetime, minutes: float) -> datetime:
    return value + timedelta(minutes=minutes)


def add_seconds(value: datetime, seconds: float) -> datetime:
    return value + timedelta(seconds=seconds)


def date_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def hour_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:00")


def iso_utc(value: datetime) -> str:
    """Serialise a naive-UTC datetime with an explicit ``Z`` suffix."""
    return to_naive_utc(value).replace(microsecond=0).isoformat() + "Z"

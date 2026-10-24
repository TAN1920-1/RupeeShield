"""Database session handling.

SQLite by default (zero infrastructure, no credentials, no cost). The engine is
created once per process; ``get_db`` is the FastAPI dependency that yields a
session per request and always closes it.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.models import Base

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine: Engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """WAL + foreign keys.

        Foreign key enforcement is OFF by default in SQLite, and relational
        integrity between incidents / timeline rows is something we rely on.

        NOTE: WAL mode is a local-filesystem feature. If the database file is on a
        network share, SQLite falls back to the default journal mode and logs a
        warning instead of failing -- acceptable for a local teaching project.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:  # pragma: no cover - driver-level fallback
            pass
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:  # pragma: no cover - network filesystem fallback
            pass
        cursor.close()


def init_db() -> None:
    """Create any missing tables. Safe to call on every startup."""
    Base.metadata.create_all(bind=engine)


def dispose_engine() -> None:
    engine.dispose()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

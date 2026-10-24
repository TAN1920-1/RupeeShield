"""Database layer.

Convenience re-exports so callers can write ``from app.db import repositories as repo``
or ``from app.db import get_db`` without reaching into the individual modules.
"""
from __future__ import annotations

from app.db.session import SessionLocal, dispose_engine, engine, get_db, init_db

__all__ = ["SessionLocal", "dispose_engine", "engine", "get_db", "init_db"]

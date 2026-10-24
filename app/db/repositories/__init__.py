"""Query helpers that sit between the services and SQLAlchemy.

Routes never build queries and services never receive raw SQL: every read/write of
the ledger goes through a function in this package. That keeps the ORM usage in one
place, makes the queries reusable by both the API and the CLI tools, and means a
schema change has exactly one layer to update.

Every function takes an open :class:`sqlalchemy.orm.Session`; transaction commits
are the caller's decision (except for the small helpers that explicitly append an
immutable row, which commit immediately).

The submodules are imported here so callers can write ``repo.runs.create_run(...)``
instead of importing each module individually.
"""
from app.db.repositories import (
    accounts,
    detections,
    governance,
    incidents,
    runs,
    transactions,
)

__all__ = [
    "accounts",
    "detections",
    "governance",
    "incidents",
    "runs",
    "transactions",
]

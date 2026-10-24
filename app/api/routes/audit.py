"""Audit trail (Module K).

Read-only access to the append-only audit log. Nothing in the application ever
updates or deletes an audit row, so "what did the system do and why" is always
answerable after the fact.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.db import repositories as repo
from app.schemas.common import Page
from app.schemas.settings import AuditLogOut
from app.security.validation import page_params

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut], summary="Audit log, newest first")
def audit_log(
    session: SessionDep,
    action: str | None = Query(default=None, max_length=60),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset)
    rows, total = repo.governance.list_audit(
        session, limit=resolved_limit, offset=resolved_offset, action=(action or None)
    )
    items = [
        {
            "id": row.id,
            "timestamp": row.timestamp,
            "actor": row.actor,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "outcome": row.outcome,
            "details": dict(row.details or {}),
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "limit": resolved_limit,
        "offset": resolved_offset,
        "returned": len(items),
    }


__all__ = ["router"]

"""Append-only audit trail.

Every state-changing action (running a simulation, changing a threshold, moving an
incident's status, replaying an incident) and every automated defensive decision is
recorded here. The audit table is never updated or deleted by application code, so
"what did the system do and why" is always answerable after the fact.

Audit writes are best-effort: a failure to log must never break the user's action
or silently report success. Failures are therefore logged loudly and the caller is
told through the return value.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AuditLog
from app.utils.ids import new_id
from app.utils.timeutils import utcnow

logger = get_logger(__name__)

MAX_DETAIL_STRING = 500


def _truncate(value: Any) -> Any:
    """Keep audit rows small and free of unbounded user text."""
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING]
    if isinstance(value, dict):
        return {str(key): _truncate(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_truncate(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_DETAIL_STRING]


def record_audit(
    session: Session,
    *,
    action: str,
    actor: str = "operator",
    entity_type: str = "",
    entity_id: str | None = None,
    outcome: str = "SUCCESS",
    details: dict[str, Any] | None = None,
) -> bool:
    """Append one audit row. Returns ``True`` when the row was written."""
    entry = AuditLog(
        id=new_id("AUDIT"),
        timestamp=utcnow(),
        actor=actor[:60],
        action=action[:60],
        entity_type=entity_type[:40],
        entity_id=(entity_id or None),
        outcome=outcome[:20],
        details=_truncate(details or {}),
    )
    try:
        session.add(entry)
        session.commit()
        return True
    except Exception as exc:  # pragma: no cover - defensive: logging must not break actions
        logger.error("Failed to write audit entry for %s: %s", action, exc)
        session.rollback()
        return False

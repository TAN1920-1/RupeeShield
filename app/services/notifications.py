"""Notification service.

Notifications are produced by the backend at the moment something actually
happens — a transfer is blocked, a login fails repeatedly, an incident is opened —
and are addressed either to one customer or to everyone holding a role. There is no
frontend timer anywhere in this path, which is the difference between an alert and
a decoration.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import repositories as repo
from app.db.models import Notification, User
from app.utils.ids import new_id
from app.utils.timeutils import iso_utc, utcnow

logger = get_logger(__name__)


def notify(
    session: Session,
    *,
    kind: str,
    title: str,
    severity: str = "INFO",
    body: str = "",
    user_id: str | None = None,
    role: str | None = None,
    entity_type: str = "",
    entity_id: str | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Append one notification, or return ``None`` when it was already raised.

    ``dedupe_key`` is what keeps a repeated observation (the same rule firing on
    the same transaction) from filling the panel with duplicates.
    """
    try:
        return repo.notifications.add_notification(
            session,
            {
                "id": new_id("NTF"),
                "user_id": user_id,
                "role": None if user_id else role,
                "kind": kind,
                "severity": severity,
                "title": title[:160],
                "body": body,
                "entity_type": entity_type[:40],
                "entity_id": entity_id,
                "dedupe_key": dedupe_key,
                "created_at": utcnow(),
            },
        )
    except Exception as exc:  # pragma: no cover - an alert must never break an action
        logger.error("Could not record notification %s: %s", kind, exc)
        session.rollback()
        return None


def shape(row: Notification) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "title": row.title,
        "body": row.body,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "created_at": iso_utc(row.created_at),
        "read": row.read_at is not None,
        "read_at": iso_utc(row.read_at) if row.read_at else None,
    }


def _audience(user: User) -> tuple[str | None, str | None]:
    """A customer sees their own messages; an analyst sees the analyst queue."""
    if user.role == "ANALYST":
        return None, "ANALYST"
    return user.id, None


def feed(
    session: Session,
    user: User,
    *,
    limit: int,
    offset: int,
    unread_only: bool = False,
) -> dict:
    user_id, role = _audience(user)
    rows, total = repo.notifications.list_notifications(
        session,
        user_id=user_id,
        role=role,
        limit=limit,
        offset=offset,
        unread_only=unread_only,
    )
    items = [shape(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
        "unread": repo.notifications.unread_count(session, user_id=user_id, role=role),
    }


def unread_count(session: Session, user: User) -> int:
    user_id, role = _audience(user)
    return repo.notifications.unread_count(session, user_id=user_id, role=role)


def mark_read(session: Session, user: User, notification_id: str) -> bool:
    user_id, role = _audience(user)
    changed = repo.notifications.mark_read(
        session, notification_id=notification_id, user_id=user_id, role=role, when=utcnow()
    )
    if changed:
        session.commit()
    return changed


def mark_all_read(session: Session, user: User) -> int:
    user_id, role = _audience(user)
    count = repo.notifications.mark_all_read(
        session, user_id=user_id, role=role, when=utcnow()
    )
    session.commit()
    return count


__all__ = ["feed", "mark_all_read", "mark_read", "notify", "shape", "unread_count"]

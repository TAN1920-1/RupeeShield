"""Read/write helpers for in-app notifications."""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Notification


def add_notification(session: Session, row: dict) -> Notification | None:
    """Append one notification unless its ``dedupe_key`` already exists.

    Returns ``None`` when the event was already announced, so callers can treat
    "raise a notification" as idempotent without checking first.
    """
    dedupe_key = row.get("dedupe_key")
    if dedupe_key and already_notified(session, dedupe_key):
        return None
    notification = Notification(**row)
    session.add(notification)
    session.flush()
    return notification


def already_notified(session: Session, dedupe_key: str) -> bool:
    return (
        session.execute(
            select(Notification.id).where(Notification.dedupe_key == dedupe_key).limit(1)
        ).scalars().first()
        is not None
    )


def _audience(user_id: str | None, role: str | None):
    """A notification is visible to one user, or to everyone holding a role."""
    if user_id:
        return Notification.user_id == user_id
    if role:
        return Notification.role == role
    return Notification.id.is_(None)  # matches nothing: a broadcast needs a role


def list_notifications(
    session: Session,
    *,
    user_id: str | None,
    role: str | None,
    limit: int,
    offset: int,
    unread_only: bool = False,
) -> tuple[list[Notification], int]:
    scope = _audience(user_id, role)
    statement = select(Notification).where(scope)
    counter = select(func.count()).select_from(Notification).where(scope)
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
        counter = counter.where(Notification.read_at.is_(None))
    total = int(session.execute(counter).scalar() or 0)
    rows = list(
        session.execute(
            statement.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return rows, total


def unread_count(session: Session, *, user_id: str | None, role: str | None) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Notification)
            .where(_audience(user_id, role), Notification.read_at.is_(None))
        ).scalar()
        or 0
    )


def mark_read(session: Session, *, notification_id: str, user_id: str | None, role: str | None, when) -> bool:
    row = session.execute(
        select(Notification).where(
            Notification.id == notification_id, _audience(user_id, role)
        )
    ).scalars().first()
    if row is None:
        return False
    if row.read_at is None:
        row.read_at = when
        session.add(row)
    return True


def mark_all_read(session: Session, *, user_id: str | None, role: str | None, when) -> int:
    rows = session.execute(
        select(Notification).where(
            _audience(user_id, role), Notification.read_at.is_(None)
        )
    ).scalars().all()
    for row in rows:
        row.read_at = when
        session.add(row)
    return len(rows)


def notifications_for_entity(
    session: Session, *, entity_type: str, entity_id: str, limit: int = 20
) -> list[Notification]:
    return list(
        session.execute(
            select(Notification)
            .where(
                Notification.entity_type == entity_type,
                Notification.entity_id == entity_id,
            )
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def recent_for_audience(
    session: Session, *, user_id: str | None, role: str | None, limit: int = 5
) -> list[Notification]:
    rows, _total = list_notifications(
        session, user_id=user_id, role=role, limit=limit, offset=0
    )
    return rows


__all__ = [
    "add_notification",
    "already_notified",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "notifications_for_entity",
    "recent_for_audience",
    "unread_count",
]

"""Notification endpoints (the notification centre).

The panel is fed by rows the backend wrote when something happened. Nothing here
generates content — this module only reads and marks as read.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.common import MessageResponse
from app.schemas.notification import NotificationFeedOut, UnreadCountOut
from app.security.validation import page_params
from app.services import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationFeedOut,
    summary="Your notifications",
    description=(
        "A customer sees the alerts raised about their own accounts; an analyst sees the "
        "security-operations queue. The response carries the unread count so the badge and "
        "the list can never disagree."
    ),
)
def list_notifications(
    session: SessionDep,
    user: CurrentUser,
    unread_only: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
) -> dict:
    resolved_limit, resolved_offset = page_params(limit, offset, default_limit=20)
    return notifications.feed(
        session,
        user,
        limit=resolved_limit,
        offset=resolved_offset,
        unread_only=unread_only,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountOut,
    summary="Unread badge count",
)
def unread_count(session: SessionDep, user: CurrentUser) -> dict:
    return {"unread": notifications.unread_count(session, user)}


@router.post(
    "/{notification_id}/read",
    response_model=MessageResponse,
    summary="Mark one notification as read",
    description="404 when the notification exists but belongs to somebody else: the scope is part of the query.",
)
def mark_read(notification_id: str, session: SessionDep, user: CurrentUser) -> dict:
    if not notifications.mark_read(session, user, notification_id):
        raise LookupError(f"No notification with id {notification_id} for this account.")
    return {"message": "Marked as read."}


@router.post(
    "/read-all",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark everything as read",
)
def mark_all_read(session: SessionDep, user: CurrentUser, response: Response) -> dict:
    marked = notifications.mark_all_read(session, user)
    response.headers["X-Marked-Read"] = str(marked)
    return {"message": f"Marked {marked} notification(s) as read."}


__all__ = ["router"]

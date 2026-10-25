"""Notification schemas."""
from __future__ import annotations

from pydantic import Field

from app.schemas.common import ApiModel, OptionalUtcDateTime, UtcDateTime


class NotificationOut(ApiModel):
    id: str
    kind: str
    severity: str
    title: str
    body: str = ""
    entity_type: str = ""
    entity_id: str | None = None
    created_at: UtcDateTime
    read: bool = False
    read_at: OptionalUtcDateTime = None


class NotificationFeedOut(ApiModel):
    """The panel payload: the page plus the unread badge count, in one response."""

    items: list[NotificationOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int
    returned: int = 0
    unread: int = 0


class UnreadCountOut(ApiModel):
    unread: int


__all__ = ["NotificationFeedOut", "NotificationOut", "UnreadCountOut"]

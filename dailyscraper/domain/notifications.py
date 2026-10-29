from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Urgency(StrEnum):
    URGENT = "urgent"
    NORMAL = "normal"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Notification:
    """A rendered message for one channel, produced from one event."""

    notification_id: str
    run_id: str
    kind: str
    coalesce_key: str
    channel: str
    urgency: Urgency
    title: str
    body: str
    cooldown_seconds: float = 0.0
    ttl_seconds: float = 3600.0


@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    run_id: str
    kind: str
    coalesce_key: str
    channel: str
    urgency: Urgency
    title: str
    body: str
    status: NotificationStatus
    created_at: str
    next_attempt_at: str
    expires_at: str
    attempts: int = 0
    event_count: int = 1
    suppressed_count: int = 0
    sent_at: str | None = None
    last_error: str | None = None

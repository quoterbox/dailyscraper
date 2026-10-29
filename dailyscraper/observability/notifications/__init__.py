"""Messages about runs to people: events → router → outbox → channels."""

from dailyscraper.observability.notifications.notifier import (
    Notifier,
    NotifierStats,
    build_notifier,
)
from dailyscraper.observability.notifications.router import (
    NotificationRouter,
    notification_kind,
    render,
)

__all__ = [
    "NotificationRouter",
    "Notifier",
    "NotifierStats",
    "build_notifier",
    "notification_kind",
    "render",
]

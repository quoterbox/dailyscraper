"""Turns a run's events into messages and sends them, beside the run and never in its way."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass

from dailyscraper.domain import Event, NotificationRecord
from dailyscraper.observability.notifications.channels import (
    Channel,
    Message,
    PermanentFailure,
    RetryAfter,
    Sent,
    build_channels,
    scrub_error,
)
from dailyscraper.observability.notifications.router import NotificationRouter, coalesced_suffix
from dailyscraper.settings.models import NotificationsSettings
from dailyscraper.storage import Store

logger = logging.getLogger(__name__)

EVENT_BATCH = 200
SEND_BATCH = 20


@dataclass
class NotifierStats:
    created: int = 0
    merged: int = 0
    suppressed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    expired: int = 0


class Notifier:
    """One consumer per run: reads the run's events after its cursor and works the outbox.

    Every public method contains its own failures: a broken messenger must
    never decide whether parsing continues.
    """

    def __init__(
        self,
        store: Store,
        run_id: str,
        router: NotificationRouter,
        channels: Mapping[str, Channel],
        settings: NotificationsSettings,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.router = router
        self.channels = dict(channels)
        self.settings = settings
        self.consumer = f"notifications:{run_id}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Notifier:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._loop, name=f"notifier-{self.run_id}", daemon=True
            )
            self._thread.start()
        return self

    def observe(self, event: Event) -> None:
        if self.router.relevant(event):
            self._wake.set()

    def stop(self) -> NotifierStats:
        """Stop the loop and send what is due, the run's final message included."""
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self.settings.drain_timeout_seconds)
        return self.run_once()

    def run_once(self) -> NotifierStats:
        stats = NotifierStats()
        try:
            self._materialize(stats)
        except Exception:
            logger.exception("Notifications could not be prepared")
        try:
            stats.expired = self.store.notifications.expire()
            for record in self.store.notifications.due(run_id=self.run_id, limit=SEND_BATCH):
                self._deliver(record, stats)
        except Exception:
            logger.exception("Notifications could not be sent")
        return stats

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self.settings.dispatch_interval_seconds)
            self._wake.clear()

    def _materialize(self, stats: NotifierStats) -> None:
        cursor = self.store.notifications.cursor(self.consumer)
        while True:
            events = self.store.events.list(self.run_id, after=cursor, limit=EVENT_BATCH)
            if not events:
                return
            notifications = [item for event in events for item in self.router.route(event)]
            cursor = events[-1].seq
            counts = self.store.notifications.enqueue(
                notifications, consumer=self.consumer, cursor=cursor
            )
            stats.created += counts["created"]
            stats.merged += counts["merged"]
            stats.suppressed += counts["suppressed"]
            if len(events) < EVENT_BATCH:
                return

    def _deliver(self, record: NotificationRecord, stats: NotifierStats) -> None:
        outbox = self.store.notifications
        channel = self.channels.get(record.channel)
        if channel is None:
            outbox.mark_failed(record.notification_id, error="channel unavailable")
            stats.failed += 1
            return
        message = Message(record.title, record.body + coalesced_suffix(record.event_count))
        try:
            outcome = channel.send(message)
        except Exception as error:
            outcome = RetryAfter(self.settings.retry_backoff_seconds, scrub_error(error))
        if isinstance(outcome, Sent):
            outbox.mark_sent(record.notification_id)
            stats.sent += 1
            logger.info("Notification %s sent via %s", record.kind, record.channel)
            return
        exhausted = record.attempts + 1 >= self.settings.max_attempts
        if isinstance(outcome, PermanentFailure) or exhausted:
            outbox.mark_failed(record.notification_id, error=outcome.reason)
            stats.failed += 1
            logger.warning(
                "Notification %s via %s failed: %s", record.kind, record.channel, outcome.reason
            )
            return
        outbox.reschedule(
            record.notification_id,
            delay_seconds=self._backoff(outcome, record.attempts + 1),
            error=outcome.reason,
        )
        stats.retried += 1

    def _backoff(self, outcome: RetryAfter, attempts: int) -> float:
        base = self.settings.retry_backoff_seconds * 2 ** (attempts - 1)
        return min(max(outcome.seconds, base), self.settings.max_retry_backoff_seconds)


def build_notifier(
    settings: NotificationsSettings, store: Store, run_id: str, variables: Mapping[str, str]
) -> Notifier | None:
    if not settings.enabled:
        return None
    channels = build_channels(settings.channels, variables)
    if not channels:
        logger.warning("Notifications are enabled but no channel is usable")
        return None
    router = NotificationRouter(settings.events, [channel.name for channel in channels])
    logger.info("Notifications via %s", ", ".join(channel.name for channel in channels))
    by_name = {channel.name: channel for channel in channels}
    return Notifier(store, run_id, router, by_name, settings)

"""Which events deserve a message, how urgent it is, and its text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from dailyscraper.domain import Event, Notification, RunStatus, Urgency
from dailyscraper.observability.notifications.channels import Message
from dailyscraper.settings.models import NotificationEvents, NotificationRule

MAX_FIELD_LENGTH = 200
MAX_SCRAPER_LINES = 20

DIRECT_KINDS = {
    "intervention.required": "intervention_required",
    "run.started": "run_started",
    "run.stopping": "run_stopped",
}
TITLES = {
    "intervention_required": "CAPTCHA: manual action required",
    "run_started": "Run started",
    "run_stopped": "Run stopping",
    "run_failed": "Run failed",
}


def notification_kind(event: Event) -> str | None:
    if event.type == "run.finished":
        return "run_failed" if event.data.get("status") == RunStatus.FAILED else "run_finished"
    return DIRECT_KINDS.get(event.type)


class NotificationRouter:
    def __init__(self, rules: NotificationEvents, channels: Sequence[str]) -> None:
        self.rules = rules
        self.channels = tuple(channels)

    def relevant(self, event: Event) -> bool:
        kind = notification_kind(event)
        return kind is not None and self._rule(kind).enabled

    def route(self, event: Event) -> list[Notification]:
        kind = notification_kind(event)
        if kind is None:
            return []
        rule = self._rule(kind)
        if not rule.enabled:
            return []
        message = render(kind, event)
        return [
            Notification(
                notification_id=f"{event.run_id}:{event.seq}:{channel}",
                run_id=event.run_id,
                kind=kind,
                # Ten scrapers on CAPTCHA in one run make one message, not ten.
                coalesce_key=f"{kind}:{event.run_id}",
                channel=channel,
                urgency=Urgency(rule.urgency),
                title=message.title,
                body=message.body,
                cooldown_seconds=rule.cooldown_seconds,
                ttl_seconds=rule.ttl_seconds,
            )
            for channel in self.channels
        ]

    def _rule(self, kind: str) -> NotificationRule:
        return getattr(self.rules, kind)


def render(kind: str, event: Event) -> Message:
    data = event.data
    lines = [f"run: {event.run_id}"]
    if event.scraper:
        lines.append(f"scraper: {event.scraper}")
    if kind == "intervention_required":
        lines += _fields(data, ("host", "host"), ("page", "page_type"), ("url", "url"))
        lines += _fields(data, ("proxy", "proxy"))
        if data.get("deadline_at"):
            lines.append(f"solve by: {_local(data['deadline_at'])}")
        lines.append(f"Solve it in the browser window on {data.get('host') or 'the machine'}.")
    elif kind in {"run_finished", "run_failed"}:
        lines += _fields(data, ("status", "status"), ("rows", "rows"))
        if data.get("duration_seconds") is not None:
            lines.append(f"duration: {_duration(float(data['duration_seconds']))}")
        lines += _scrapers(data.get("scrapers"))
        lines += _fields(data, ("error", "error"), ("output", "output"))
    elif kind == "run_started":
        lines += _fields(data, ("host", "host"))
        lines += _scrapers({name: None for name in data.get("scrapers") or ()})
    elif kind == "run_stopped":
        lines += _fields(data, ("reason", "reason"))
    lines.append(f"at: {_local(event.at)}")
    title = TITLES.get(kind) or f"Run finished: {data.get('status', 'unknown')}"
    return Message(title=title, body="\n".join(lines))


def coalesced_suffix(event_count: int) -> str:
    if event_count <= 1:
        return ""
    return f"\n(+{event_count - 1} more of the same while this message waited)"


def _fields(data: Mapping[str, Any], *mapping: tuple[str, str]) -> list[str]:
    return [
        f"{label}: {_truncate(data[key])}"
        for label, key in mapping
        if data.get(key) not in (None, "")
    ]


def _scrapers(scrapers: Any) -> list[str]:
    if not isinstance(scrapers, Mapping) or not scrapers:
        return []
    lines = [f"scrapers ({len(scrapers)}):"]
    for name, rows in list(scrapers.items())[:MAX_SCRAPER_LINES]:
        lines.append(f"  {_truncate(name)}" + ("" if rows is None else f": {rows} rows"))
    if len(scrapers) > MAX_SCRAPER_LINES:
        lines.append(f"  … {len(scrapers) - MAX_SCRAPER_LINES} more")
    return lines


def _local(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value).astimezone()
    except ValueError:
        return value
    offset = moment.strftime("%z")
    return f"{moment:%d.%m.%Y %H:%M:%S} UTC{offset[:3]}:{offset[3:]}"


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"


def _truncate(value: object) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= MAX_FIELD_LENGTH else f"{text[: MAX_FIELD_LENGTH - 1]}…"

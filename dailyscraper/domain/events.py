from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

EVENT_SCHEMA_VERSION = 1


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    project_id: str
    type: str
    at: str
    level: EventLevel = EventLevel.INFO
    scraper: str | None = None
    data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: int = EVENT_SCHEMA_VERSION


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    run_id: str
    name: str
    kind: str
    status: DeliveryStatus
    attempts: int
    at: str
    location: str | None = None
    files: tuple[str, ...] = ()
    error: str | None = None

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from dailyscraper.domain.errors import ErrorCategory


class TaskKind(StrEnum):
    LIST = "list"
    DETAIL = "detail"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        return self in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class AttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _empty() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True)
class TaskSpec:
    """``payload``: ``{"link": url}`` for a list task, ``{"rows": [...]}`` for a detail task.

    Detail rows are 1-based positions in the run's input snapshot.
    """

    task_id: str
    run_id: str
    scraper: str
    kind: TaskKind
    payload: Mapping[str, Any]
    priority: int = 0
    max_attempts: int = 3


@dataclass(frozen=True)
class Task:
    task_id: str
    run_id: str
    scraper: str
    kind: TaskKind
    payload: Mapping[str, Any]
    status: TaskStatus
    attempts: int
    max_attempts: int
    priority: int = 0
    not_before: str | None = None
    avoid: tuple[str, ...] = ()
    last_category: ErrorCategory | None = None
    last_error: str | None = None

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self.attempts)


@dataclass(frozen=True)
class TaskOutcome:
    """What one attempt achieved; ``retry_rows`` are detail inputs worth another attempt."""

    status: AttemptStatus
    category: ErrorCategory | None = None
    rows: int = 0
    error: str | None = None
    retry_rows: tuple[int, ...] = ()
    failures: tuple[Mapping[str, Any], ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    task_id: str
    run_id: str
    attempt_no: int
    slot_id: str
    host_id: str
    status: AttemptStatus
    started_at: str
    proxy_id: str | None = None
    finished_at: str | None = None
    category: ErrorCategory | None = None
    rows: int = 0
    error: str | None = None
    details: Mapping[str, Any] = field(default_factory=_empty)


class CommandKind(StrEnum):
    STOP = "stop"
    KILL = "kill"


@dataclass(frozen=True)
class Command:
    command_id: str
    run_id: str
    kind: CommandKind
    created_at: str
    applied_at: str | None = None

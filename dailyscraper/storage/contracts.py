from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, Protocol

from dailyscraper.domain import (
    Attempt,
    BatchCommit,
    Command,
    CommandKind,
    CommitRecord,
    DeliveryRecord,
    Event,
    LeaseKey,
    PartitionRef,
    PartitionSeal,
    PartitionState,
    ProxyHealthState,
    RunRecord,
    RunStatus,
    Task,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
)


class StorageError(RuntimeError):
    pass


class CommitConflict(StorageError):
    """The batch does not continue the partition's registered committed size."""


class RunRepository(Protocol):
    def create(self, run: RunRecord) -> None: ...

    def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        summary: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> RunRecord: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def list(self, *, project_id: str | None = None, limit: int = 20) -> list[RunRecord]: ...


class PartitionRegistry(Protocol):
    def commit(self, batch: BatchCommit) -> bool:
        """Register a batch; ``False`` when the same commit was registered before."""
        ...

    def seal(self, seal: PartitionSeal) -> None: ...

    def state(self, partition: PartitionRef) -> PartitionState | None: ...

    def partitions(self, run_id: str, scraper: str | None = None) -> list[PartitionState]: ...

    def commits(self, run_id: str, scraper: str) -> list[CommitRecord]:
        """Commits in registration order."""
        ...

    def item_statuses(
        self, run_id: str, scraper: str, *, attempt_id: str | None = None
    ) -> dict[int, str]:
        """Latest status of each registered detail input row, optionally of one attempt."""
        ...


class TaskRepository(Protocol):
    def enqueue(self, specs: Sequence[TaskSpec]) -> None: ...

    def claim(self, run_id: str, *, scrapers: Collection[str]) -> Task | None:
        """Take the next due pending task of one of ``scrapers`` and mark it running.

        The least busy scraper goes first, then priority, then enqueue order.
        """
        ...

    def start(
        self, task_id: str, *, slot_id: str, host_id: str, proxy_id: str | None = None
    ) -> Attempt: ...

    def release(self, task_id: str) -> None:
        """Return a claimed task that never started to the pending ones."""
        ...

    def finish(self, attempt_id: str, outcome: TaskOutcome) -> Task:
        """Close the attempt; the task becomes final with the outcome's status."""
        ...

    def reschedule(
        self,
        attempt_id: str,
        outcome: TaskOutcome,
        *,
        not_before: str,
        payload: Mapping[str, Any] | None = None,
        avoid: Sequence[str] = (),
    ) -> Task:
        """Close the attempt and make the task pending again, due at ``not_before``."""
        ...

    def cancel_pending(self, run_id: str) -> int: ...

    def get(self, task_id: str) -> Task | None: ...

    def list(self, run_id: str) -> list[Task]: ...

    def attempts(self, run_id: str, *, task_id: str | None = None) -> list[Attempt]: ...

    def counts(self, run_id: str) -> dict[TaskStatus, int]: ...


class CommandLog(Protocol):
    def issue(self, run_id: str, kind: CommandKind) -> Command: ...

    def pending(self, run_id: str) -> list[Command]: ...

    def mark_applied(self, command_id: str) -> None: ...


class LeaseRepository(Protocol):
    """Places in limited counters, shared by every run and machine using the store.

    A holder (a slot) takes all its keys at once or none; an expired lease
    frees its places without an explicit release.
    """

    def acquire(
        self, holder: str, keys: Sequence[LeaseKey], *, run_id: str, ttl_seconds: float
    ) -> list[str]:
        """Take a place in every key; returns the keys that are full, nothing taken then."""
        ...

    def blocked(self, keys: Sequence[LeaseKey]) -> list[str]:
        """Keys without a free place, without taking any."""
        ...

    def renew(self, holders: Sequence[str], *, ttl_seconds: float) -> None: ...

    def release(self, holder: str) -> None: ...

    def count(self, key: str) -> int: ...


class EventLog(Protocol):
    def append(self, events: Sequence[Event]) -> None: ...

    def list(self, run_id: str, *, types: Sequence[str] | None = None) -> list[Event]:
        """Events in append order."""
        ...


class DeliveryLog(Protocol):
    def record(self, delivery: DeliveryRecord) -> None: ...

    def list(self, run_id: str) -> list[DeliveryRecord]: ...


class ProxyHealthRepository(Protocol):
    """Health of a proxy for a site, shared by all runs; unknown pairs start fresh."""

    def states(self, site: str, proxy_ids: Sequence[str]) -> dict[str, ProxyHealthState]: ...

    def update(
        self,
        site: str,
        proxy_id: str,
        change: Callable[[ProxyHealthState, str], ProxyHealthState],
    ) -> tuple[ProxyHealthState, ProxyHealthState]:
        """Apply ``change(state, now)`` atomically; returns the state before and after."""
        ...


class Store(Protocol):
    runs: RunRepository
    proxy_health: ProxyHealthRepository
    partitions: PartitionRegistry
    tasks: TaskRepository
    commands: CommandLog
    leases: LeaseRepository
    events: EventLog
    deliveries: DeliveryLog

    def now(self) -> str:
        """Current time from the storage's clock, ISO 8601 in UTC."""
        ...

    def close(self) -> None: ...

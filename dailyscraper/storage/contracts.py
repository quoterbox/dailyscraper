from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from dailyscraper.domain import (
    BatchCommit,
    CommitRecord,
    DeliveryRecord,
    Event,
    PartitionRef,
    PartitionSeal,
    PartitionState,
    RunRecord,
    RunStatus,
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


class EventLog(Protocol):
    def append(self, events: Sequence[Event]) -> None: ...

    def list(self, run_id: str, *, types: Sequence[str] | None = None) -> list[Event]:
        """Events in append order."""
        ...


class DeliveryLog(Protocol):
    def record(self, delivery: DeliveryRecord) -> None: ...

    def list(self, run_id: str) -> list[DeliveryRecord]: ...


class Store(Protocol):
    runs: RunRepository
    partitions: PartitionRegistry
    events: EventLog
    deliveries: DeliveryLog

    def now(self) -> str:
        """Current time from the storage's clock, ISO 8601 in UTC."""
        ...

    def close(self) -> None: ...

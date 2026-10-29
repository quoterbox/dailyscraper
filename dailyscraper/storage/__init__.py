"""Storage contracts and their implementations; SQLite and local files first."""

from pathlib import Path

from dailyscraper.storage.contracts import (
    CommitConflict,
    DeliveryLog,
    EventLog,
    NotificationOutbox,
    PartitionRegistry,
    RunRepository,
    StorageError,
    Store,
)
from dailyscraper.storage.layout import RunLayout
from dailyscraper.storage.records import PartitionIntegrityError, iter_committed_records


def open_store(backend: str, database: Path, *, busy_timeout_ms: int = 30_000) -> Store:
    if backend == "sqlite":
        from dailyscraper.storage.sqlite import SQLiteStore

        return SQLiteStore(database, busy_timeout_ms=busy_timeout_ms)
    raise StorageError(f"unknown storage backend {backend!r}")


__all__ = [
    "CommitConflict",
    "DeliveryLog",
    "EventLog",
    "NotificationOutbox",
    "PartitionIntegrityError",
    "PartitionRegistry",
    "RunLayout",
    "RunRepository",
    "StorageError",
    "Store",
    "iter_committed_records",
    "open_store",
]

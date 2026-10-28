from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dailyscraper.storage.sqlite.database import Database
from dailyscraper.storage.sqlite.repositories import (
    SQLiteDeliveries,
    SQLiteEvents,
    SQLitePartitions,
    SQLiteRuns,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SQLiteStore:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 30_000,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        db = Database(self.path, busy_timeout_ms=busy_timeout_ms)
        self.runs = SQLiteRuns(db, clock)
        self.partitions = SQLitePartitions(db, clock)
        self.events = SQLiteEvents(db)
        self.deliveries = SQLiteDeliveries(db)

    def now(self) -> str:
        return self._clock()

    def close(self) -> None:
        pass

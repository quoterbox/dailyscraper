from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dailyscraper.storage.sqlite.migrations import apply_migrations
from dailyscraper.testing.fault_injection import FaultPoint, get_fault_injector

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 0.05
RETRY_BACKOFF = 2.0
MAX_ATTEMPTS = 6


class Database:
    """Short-lived connections, one per operation; writes take the lock up front.

    ``BEGIN IMMEDIATE`` avoids the deadlock of two readers upgrading to
    writers. A busy or locked database retries the whole operation, so every
    write function must be safe to run again from the start.
    """

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 30_000,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._sleep = sleep
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            apply_migrations(connection)

    def write[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return self._retrying("write", operation, immediate=True)

    def read[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return self._retrying("read", operation, immediate=False)

    def _retrying[T](
        self, kind: str, operation: Callable[[sqlite3.Connection], T], *, immediate: bool
    ) -> T:
        delay = RETRY_DELAY_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                get_fault_injector().hit(
                    FaultPoint.SQLITE_BEFORE_OPERATION,
                    database_name=self.path.name,
                    operation=kind,
                    retry_attempt=attempt,
                )
                with self._transaction(immediate=immediate) as connection:
                    return operation(connection)
            except sqlite3.OperationalError as error:
                if attempt >= MAX_ATTEMPTS or not is_busy_error(error):
                    raise
                logger.warning("SQLite %s retry %s/%s: %s", kind, attempt, MAX_ATTEMPTS, error)
                self._sleep(delay)
                delay *= RETRY_BACKOFF
        raise AssertionError("unreachable")

    @contextmanager
    def _transaction(self, *, immediate: bool) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            connection.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        per_attempt_ms = max(1, self.busy_timeout_ms // MAX_ATTEMPTS)
        connection = sqlite3.connect(self.path, timeout=per_attempt_ms / 1000, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={per_attempt_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()


def is_busy_error(error: BaseException) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(error).lower()
    return "database is locked" in message or "database is busy" in message


def dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)

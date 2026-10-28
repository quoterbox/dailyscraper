from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dailyscraper.storage.sqlite.database import Database, is_busy_error
from dailyscraper.storage.sqlite.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    apply_migrations,
)


def test_new_database_is_migrated_once(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    Database(path)
    Database(path)

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == MIGRATIONS[-1].version
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"runs", "partitions", "commits", "events", "deliveries"} <= tables


def test_database_from_newer_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA user_version=99")

    with pytest.raises(MigrationError, match="newer"):
        apply_migrations(connection)


def test_later_migration_is_applied_on_top(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "store.sqlite", isolation_level=None)
    apply_migrations(connection)
    extra = Migration(MIGRATIONS[-1].version + 1, "extra", "CREATE TABLE extra (id TEXT);")

    assert apply_migrations(connection, (*MIGRATIONS, extra)) == extra.version


def test_busy_database_is_retried_from_the_start(tmp_path: Path) -> None:
    sleeps: list[float] = []
    db = Database(tmp_path / "store.sqlite", sleep=sleeps.append)
    attempts = []

    def operation(connection):
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "done"

    assert db.write(operation) == "done"
    assert len(attempts) == 3 and len(sleeps) == 2


def test_other_errors_are_not_retried(tmp_path: Path) -> None:
    db = Database(tmp_path / "store.sqlite", sleep=lambda s: None)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.read(lambda c: c.execute("SELECT * FROM missing").fetchall())


def test_failed_write_is_rolled_back(tmp_path: Path) -> None:
    db = Database(tmp_path / "store.sqlite")

    def operation(connection):
        connection.execute(
            "INSERT INTO deliveries VALUES ('d', 'r', 'n', 'k', 's', 1, 'now', NULL, '[]', NULL)"
        )
        raise RuntimeError("after insert")

    with pytest.raises(RuntimeError):
        db.write(operation)
    assert db.read(lambda c: c.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]) == 0


def test_busy_detection() -> None:
    assert is_busy_error(sqlite3.OperationalError("database is locked"))
    assert not is_busy_error(sqlite3.OperationalError("no such table: x"))

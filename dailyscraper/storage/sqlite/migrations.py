from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


BASELINE = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    scrapers_json TEXT NOT NULL,
    packages_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_json TEXT,
    error TEXT
);
CREATE INDEX runs_by_project ON runs(project_id, started_at);

CREATE TABLE partitions (
    run_id TEXT NOT NULL,
    scraper TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    partition_no INTEGER NOT NULL,
    path TEXT NOT NULL,
    committed_bytes INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0,
    checksum TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, scraper, writer_id, partition_no)
);

CREATE TABLE commits (
    commit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scraper TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    partition_no INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    first_record INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
CREATE INDEX commits_by_scraper ON commits(run_id, scraper, seq);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    level TEXT NOT NULL,
    scraper TEXT,
    at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE (run_id, seq)
);

CREATE TABLE deliveries (
    delivery_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    at TEXT NOT NULL,
    location TEXT,
    files_json TEXT NOT NULL,
    error TEXT
);
CREATE INDEX deliveries_by_run ON deliveries(run_id, at);
"""

MIGRATIONS: tuple[Migration, ...] = (Migration(1, "baseline", BASELINE),)


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(
    connection: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS
) -> int:
    latest = migrations[-1].version if migrations else 0
    for migration in migrations:
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated while this one waited for the lock.
            current = schema_version(connection)
            if current > latest:
                raise MigrationError(f"database schema {current} is newer than supported {latest}")
            if current < migration.version:
                if current != migration.version - 1:
                    raise MigrationError(
                        f"cannot apply migration {migration.version} to schema {current}"
                    )
                for statement in _statements(migration.sql):
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version={migration.version}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return schema_version(connection)


def _statements(script: str) -> list[str]:
    statements, current = [], ""
    for line in script.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            if current.strip():
                statements.append(current.strip())
            current = ""
    if current.strip():
        raise MigrationError("incomplete SQL statement in migration")
    return statements

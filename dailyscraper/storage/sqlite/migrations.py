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

TASKS = """
ALTER TABLE runs ADD COLUMN host_id TEXT;
ALTER TABLE runs ADD COLUMN pid INTEGER;
ALTER TABLE commits ADD COLUMN task_id TEXT;
ALTER TABLE commits ADD COLUMN attempt_id TEXT;

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scraper TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    not_before TEXT,
    avoid_json TEXT NOT NULL,
    last_category TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
CREATE INDEX tasks_due ON tasks(run_id, status, scraper, not_before);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    slot_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    proxy_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    category TEXT,
    rows INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    details_json TEXT NOT NULL,
    UNIQUE (task_id, attempt_no)
);
CREATE INDEX attempts_by_run ON attempts(run_id, started_at);

CREATE TABLE item_statuses (
    run_id TEXT NOT NULL,
    scraper TEXT NOT NULL,
    input_row INTEGER NOT NULL,
    attempt_id TEXT,
    status TEXT NOT NULL,
    commit_id TEXT NOT NULL,
    PRIMARY KEY (run_id, scraper, input_row)
);
CREATE INDEX item_statuses_by_attempt ON item_statuses(attempt_id);

CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT
);
CREATE INDEX commands_pending ON commands(run_id, applied_at);

CREATE TABLE leases (
    holder TEXT NOT NULL,
    lease_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (holder, lease_key)
);
CREATE INDEX leases_by_key ON leases(lease_key, expires_at);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline", BASELINE),
    Migration(2, "tasks_attempts_commands_leases", TASKS),
)


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

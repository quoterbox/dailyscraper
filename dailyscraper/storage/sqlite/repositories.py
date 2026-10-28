from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from dailyscraper.domain import (
    Attempt,
    AttemptStatus,
    BatchCommit,
    Command,
    CommandKind,
    CommitRecord,
    DeliveryRecord,
    DeliveryStatus,
    ErrorCategory,
    Event,
    EventLevel,
    LeaseKey,
    PartitionRef,
    PartitionSeal,
    PartitionState,
    RunRecord,
    RunStatus,
    Task,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
)
from dailyscraper.storage.contracts import CommitConflict, StorageError
from dailyscraper.storage.sqlite.database import Database, dumps, loads

Clock = Callable[[], str]


class SQLiteRuns:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def create(self, run: RunRecord) -> None:
        self._db.write(
            lambda c: c.execute(
                "INSERT INTO runs (run_id, project_id, status, started_at, finished_at,"
                " scrapers_json, packages_json, config_json, summary_json, error, host_id, pid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.project_id,
                    run.status.value,
                    run.started_at,
                    run.finished_at,
                    dumps(list(run.scrapers)),
                    dumps(dict(run.packages)),
                    dumps(dict(run.config)),
                    dumps(dict(run.summary)) if run.summary is not None else None,
                    run.error,
                    run.host_id,
                    run.pid,
                ),
            )
        )

    def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        summary: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> RunRecord:
        def operation(c: sqlite3.Connection) -> None:
            updated = c.execute(
                "UPDATE runs SET status = ?, finished_at = ?, summary_json = ?, error = ?"
                " WHERE run_id = ?",
                (status.value, self._clock(), dumps(summary), error, run_id),
            )
            if updated.rowcount != 1:
                raise StorageError(f"run {run_id} does not exist")

        self._db.write(operation)
        run = self.get(run_id)
        assert run is not None
        return run

    def get(self, run_id: str) -> RunRecord | None:
        row = self._db.read(
            lambda c: c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        )
        return _run(row) if row is not None else None

    def list(self, *, project_id: str | None = None, limit: int = 20) -> list[RunRecord]:
        query = "SELECT * FROM runs"
        params: tuple[Any, ...] = ()
        if project_id is not None:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
        rows = self._db.read(lambda c: c.execute(query, (*params, limit)).fetchall())
        return [_run(row) for row in rows]


class SQLitePartitions:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def commit(self, batch: BatchCommit) -> bool:
        ref = batch.partition

        def operation(c: sqlite3.Connection) -> bool:
            if c.execute(
                "SELECT 1 FROM commits WHERE commit_id = ?", (batch.commit_id,)
            ).fetchone():
                return False
            row = c.execute(
                "SELECT committed_bytes, sealed FROM partitions"
                " WHERE run_id = ? AND scraper = ? AND writer_id = ? AND partition_no = ?",
                _key(ref),
            ).fetchone()
            committed = row["committed_bytes"] if row else 0
            if row and row["sealed"]:
                raise CommitConflict(f"partition {ref.path} is sealed")
            if batch.start_offset != committed:
                raise CommitConflict(
                    f"partition {ref.path}: batch starts at {batch.start_offset},"
                    f" registered size is {committed}"
                )
            now = self._clock()
            if row is None:
                c.execute(
                    "INSERT INTO partitions (run_id, scraper, writer_id, partition_no, path,"
                    " committed_bytes, rows, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*_key(ref), ref.path, batch.end_offset, batch.rows, now),
                )
            else:
                c.execute(
                    "UPDATE partitions SET committed_bytes = ?, rows = rows + ?, updated_at = ?"
                    " WHERE run_id = ? AND scraper = ? AND writer_id = ? AND partition_no = ?",
                    (batch.end_offset, batch.rows, now, *_key(ref)),
                )
            seq = c.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM commits WHERE run_id = ?", (ref.run_id,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO commits (commit_id, run_id, scraper, writer_id, partition_no, seq,"
                " start_offset, end_offset, first_record, rows, checksum, created_at,"
                " task_id, attempt_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch.commit_id,
                    *_key(ref),
                    seq,
                    batch.start_offset,
                    batch.end_offset,
                    batch.first_record,
                    batch.rows,
                    batch.checksum,
                    now,
                    batch.task_id,
                    batch.attempt_id,
                ),
            )
            c.executemany(
                "INSERT INTO item_statuses (run_id, scraper, input_row, attempt_id, status,"
                " commit_id) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (run_id, scraper, input_row) DO UPDATE SET"
                " attempt_id = excluded.attempt_id, status = excluded.status,"
                " commit_id = excluded.commit_id",
                [
                    (ref.run_id, ref.scraper, row, batch.attempt_id, status, batch.commit_id)
                    for row, status in batch.items
                ],
            )
            return True

        return self._db.write(operation)

    def seal(self, seal: PartitionSeal) -> None:
        ref = seal.partition

        def operation(c: sqlite3.Connection) -> None:
            row = c.execute(
                "SELECT committed_bytes FROM partitions"
                " WHERE run_id = ? AND scraper = ? AND writer_id = ? AND partition_no = ?",
                _key(ref),
            ).fetchone()
            if row is None:
                raise StorageError(f"partition {ref.path} has no commits")
            if row["committed_bytes"] != seal.size_bytes:
                raise CommitConflict(
                    f"partition {ref.path}: sealed size {seal.size_bytes},"
                    f" registered size is {row['committed_bytes']}"
                )
            c.execute(
                "UPDATE partitions SET sealed = 1, checksum = ?, updated_at = ?"
                " WHERE run_id = ? AND scraper = ? AND writer_id = ? AND partition_no = ?",
                (seal.checksum, self._clock(), *_key(ref)),
            )

        self._db.write(operation)

    def state(self, partition: PartitionRef) -> PartitionState | None:
        row = self._db.read(
            lambda c: c.execute(
                "SELECT * FROM partitions"
                " WHERE run_id = ? AND scraper = ? AND writer_id = ? AND partition_no = ?",
                _key(partition),
            ).fetchone()
        )
        return _partition(row) if row is not None else None

    def partitions(self, run_id: str, scraper: str | None = None) -> list[PartitionState]:
        query = "SELECT * FROM partitions WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if scraper is not None:
            query += " AND scraper = ?"
            params += (scraper,)
        query += " ORDER BY scraper, writer_id, partition_no"
        rows = self._db.read(lambda c: c.execute(query, params).fetchall())
        return [_partition(row) for row in rows]

    def commits(self, run_id: str, scraper: str) -> list[CommitRecord]:
        rows = self._db.read(
            lambda c: c.execute(
                "SELECT c.*, p.path FROM commits c JOIN partitions p"
                " ON p.run_id = c.run_id AND p.scraper = c.scraper"
                " AND p.writer_id = c.writer_id AND p.partition_no = c.partition_no"
                " WHERE c.run_id = ? AND c.scraper = ? ORDER BY c.seq",
                (run_id, scraper),
            ).fetchall()
        )
        return [
            CommitRecord(
                commit_id=row["commit_id"],
                partition=_ref(row),
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
                first_record=row["first_record"],
                rows=row["rows"],
                checksum=row["checksum"],
                task_id=row["task_id"],
                attempt_id=row["attempt_id"],
            )
            for row in rows
        ]

    def item_statuses(
        self, run_id: str, scraper: str, *, attempt_id: str | None = None
    ) -> dict[int, str]:
        query = "SELECT input_row, status FROM item_statuses WHERE run_id = ? AND scraper = ?"
        params: tuple[Any, ...] = (run_id, scraper)
        if attempt_id is not None:
            query += " AND attempt_id = ?"
            params += (attempt_id,)
        rows = self._db.read(lambda c: c.execute(query, params).fetchall())
        return {row["input_row"]: row["status"] for row in rows}


class SQLiteTasks:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def enqueue(self, specs: Sequence[TaskSpec]) -> None:
        if not specs:
            return

        def operation(c: sqlite3.Connection) -> None:
            now = self._clock()
            next_seq: dict[str, int] = {}
            for spec in specs:
                if spec.run_id not in next_seq:
                    next_seq[spec.run_id] = c.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 FROM tasks WHERE run_id = ?",
                        (spec.run_id,),
                    ).fetchone()[0]
                c.execute(
                    "INSERT INTO tasks (task_id, run_id, scraper, kind, payload_json, status,"
                    " priority, seq, attempts, max_attempts, avoid_json, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, '[]', ?)",
                    (
                        spec.task_id,
                        spec.run_id,
                        spec.scraper,
                        spec.kind.value,
                        dumps(dict(spec.payload)),
                        TaskStatus.PENDING.value,
                        spec.priority,
                        next_seq[spec.run_id],
                        spec.max_attempts,
                        now,
                    ),
                )
                next_seq[spec.run_id] += 1

        self._db.write(operation)

    def claim(self, run_id: str, *, scrapers: Collection[str]) -> Task | None:
        if not scrapers:
            return None
        names = list(scrapers)

        def operation(c: sqlite3.Connection) -> Task | None:
            now = self._clock()
            row = c.execute(
                "SELECT t.* FROM tasks t WHERE t.run_id = ? AND t.status = ?"
                f" AND t.scraper IN ({', '.join('?' for _ in names)})"
                " AND (t.not_before IS NULL OR t.not_before <= ?)"
                " ORDER BY (SELECT COUNT(*) FROM tasks r WHERE r.run_id = t.run_id"
                " AND r.scraper = t.scraper AND r.status = ?), t.priority DESC, t.seq"
                " LIMIT 1",
                (run_id, TaskStatus.PENDING.value, *names, now, TaskStatus.RUNNING.value),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.RUNNING.value, now, row["task_id"]),
            )
            return _task(
                c.execute("SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)).fetchone()
            )

        return self._db.write(operation)

    def start(
        self, task_id: str, *, slot_id: str, host_id: str, proxy_id: str | None = None
    ) -> Attempt:
        def operation(c: sqlite3.Connection) -> Attempt:
            task = c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None or task["status"] != TaskStatus.RUNNING.value:
                raise StorageError(f"task {task_id} is not claimed")
            attempt_no = task["attempts"] + 1
            attempt_id = f"{task_id}#{attempt_no}"
            now = self._clock()
            c.execute(
                "UPDATE tasks SET attempts = ?, updated_at = ? WHERE task_id = ?",
                (attempt_no, now, task_id),
            )
            c.execute(
                "INSERT INTO attempts (attempt_id, task_id, run_id, attempt_no, slot_id, host_id,"
                " proxy_id, status, started_at, details_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')",
                (
                    attempt_id,
                    task_id,
                    task["run_id"],
                    attempt_no,
                    slot_id,
                    host_id,
                    proxy_id,
                    AttemptStatus.RUNNING.value,
                    now,
                ),
            )
            return _attempt(
                c.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            )

        return self._db.write(operation)

    def release(self, task_id: str) -> None:
        self._db.write(
            lambda c: c.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
                (TaskStatus.PENDING.value, self._clock(), task_id, TaskStatus.RUNNING.value),
            )
        )

    def finish(self, attempt_id: str, outcome: TaskOutcome) -> Task:
        status = {
            AttemptStatus.COMPLETED: TaskStatus.COMPLETED,
            AttemptStatus.CANCELLED: TaskStatus.CANCELLED,
        }.get(outcome.status, TaskStatus.FAILED)
        return self._close(attempt_id, outcome, status=status)

    def reschedule(
        self,
        attempt_id: str,
        outcome: TaskOutcome,
        *,
        not_before: str,
        payload: Mapping[str, Any] | None = None,
        avoid: Sequence[str] = (),
    ) -> Task:
        return self._close(
            attempt_id,
            outcome,
            status=TaskStatus.PENDING,
            not_before=not_before,
            payload=payload,
            avoid=avoid,
        )

    def _close(
        self,
        attempt_id: str,
        outcome: TaskOutcome,
        *,
        status: TaskStatus,
        not_before: str | None = None,
        payload: Mapping[str, Any] | None = None,
        avoid: Sequence[str] = (),
    ) -> Task:
        category = outcome.category.value if outcome.category else None

        def operation(c: sqlite3.Connection) -> Task:
            attempt = c.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise StorageError(f"attempt {attempt_id} does not exist")
            task_id = attempt["task_id"]
            if attempt["status"] != AttemptStatus.RUNNING.value:
                return _task(
                    c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                )
            now = self._clock()
            c.execute(
                "UPDATE attempts SET status = ?, finished_at = ?, category = ?, rows = ?,"
                " error = ?, details_json = ? WHERE attempt_id = ?",
                (
                    outcome.status.value,
                    now,
                    category,
                    outcome.rows,
                    outcome.error,
                    dumps(dict(outcome.details)),
                    attempt_id,
                ),
            )
            task = c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            avoided = list(dict.fromkeys([*loads(task["avoid_json"]), *avoid]))
            c.execute(
                "UPDATE tasks SET status = ?, not_before = ?, payload_json = ?, avoid_json = ?,"
                " last_category = ?, last_error = ?, updated_at = ? WHERE task_id = ?",
                (
                    status.value,
                    not_before,
                    dumps(dict(payload)) if payload is not None else task["payload_json"],
                    dumps(avoided),
                    category,
                    outcome.error,
                    now,
                    task_id,
                ),
            )
            return _task(c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())

        return self._db.write(operation)

    def cancel_pending(self, run_id: str) -> int:
        return self._db.write(
            lambda c: (
                c.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE run_id = ? AND status = ?",
                    (TaskStatus.CANCELLED.value, self._clock(), run_id, TaskStatus.PENDING.value),
                ).rowcount
            )
        )

    def get(self, task_id: str) -> Task | None:
        row = self._db.read(
            lambda c: c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        )
        return _task(row) if row is not None else None

    def list(self, run_id: str) -> list[Task]:
        rows = self._db.read(
            lambda c: c.execute(
                "SELECT * FROM tasks WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        )
        return [_task(row) for row in rows]

    def attempts(self, run_id: str, *, task_id: str | None = None) -> list[Attempt]:
        query = "SELECT * FROM attempts WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if task_id is not None:
            query += " AND task_id = ?"
            params += (task_id,)
        rows = self._db.read(
            lambda c: c.execute(query + " ORDER BY started_at, attempt_id", params).fetchall()
        )
        return [_attempt(row) for row in rows]

    def counts(self, run_id: str) -> dict[TaskStatus, int]:
        rows = self._db.read(
            lambda c: c.execute(
                "SELECT status, COUNT(*) AS n FROM tasks WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall()
        )
        return {TaskStatus(row["status"]): row["n"] for row in rows}


class SQLiteCommands:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def issue(self, run_id: str, kind: CommandKind) -> Command:
        command = Command(
            command_id=uuid.uuid4().hex, run_id=run_id, kind=kind, created_at=self._clock()
        )
        self._db.write(
            lambda c: c.execute(
                "INSERT INTO commands (command_id, run_id, kind, created_at) VALUES (?, ?, ?, ?)",
                (command.command_id, run_id, kind.value, command.created_at),
            )
        )
        return command

    def pending(self, run_id: str) -> list[Command]:
        rows = self._db.read(
            lambda c: c.execute(
                "SELECT * FROM commands WHERE run_id = ? AND applied_at IS NULL"
                " ORDER BY created_at, command_id",
                (run_id,),
            ).fetchall()
        )
        return [
            Command(
                command_id=row["command_id"],
                run_id=row["run_id"],
                kind=CommandKind(row["kind"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def mark_applied(self, command_id: str) -> None:
        self._db.write(
            lambda c: c.execute(
                "UPDATE commands SET applied_at = ? WHERE command_id = ? AND applied_at IS NULL",
                (self._clock(), command_id),
            )
        )


class SQLiteLeases:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def acquire(
        self, holder: str, keys: Sequence[LeaseKey], *, run_id: str, ttl_seconds: float
    ) -> list[str]:
        def operation(c: sqlite3.Connection) -> list[str]:
            now = self._clock()
            c.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
            full = [key.key for key in keys if _full(c, key, holder)]
            if full:
                return full
            expires = _after(now, ttl_seconds)
            c.executemany(
                "INSERT INTO leases (holder, lease_key, run_id, expires_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (holder, lease_key) DO UPDATE SET expires_at = excluded.expires_at",
                [(holder, key.key, run_id, expires) for key in keys],
            )
            return []

        return self._db.write(operation)

    def blocked(self, keys: Sequence[LeaseKey]) -> list[str]:
        now = self._clock()

        def operation(c: sqlite3.Connection) -> list[str]:
            return [
                key.key
                for key in keys
                if c.execute(
                    "SELECT COUNT(*) FROM leases WHERE lease_key = ? AND expires_at > ?",
                    (key.key, now),
                ).fetchone()[0]
                >= key.limit
            ]

        return self._db.read(operation)

    def renew(self, holders: Sequence[str], *, ttl_seconds: float) -> None:
        if not holders:
            return
        expires = _after(self._clock(), ttl_seconds)
        self._db.write(
            lambda c: c.executemany(
                "UPDATE leases SET expires_at = ? WHERE holder = ?",
                [(expires, holder) for holder in holders],
            )
        )

    def release(self, holder: str) -> None:
        self._db.write(lambda c: c.execute("DELETE FROM leases WHERE holder = ?", (holder,)))

    def count(self, key: str) -> int:
        now = self._clock()
        return self._db.read(
            lambda c: c.execute(
                "SELECT COUNT(*) FROM leases WHERE lease_key = ? AND expires_at > ?", (key, now)
            ).fetchone()[0]
        )


def _full(c: sqlite3.Connection, key: LeaseKey, holder: str) -> bool:
    others = c.execute(
        "SELECT COUNT(*) FROM leases WHERE lease_key = ? AND holder != ?", (key.key, holder)
    ).fetchone()[0]
    return others >= key.limit


class SQLiteEvents:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, events: Sequence[Event]) -> None:
        if not events:
            return

        def operation(c: sqlite3.Connection) -> None:
            next_seq: dict[str, int] = {}
            for event in events:
                if event.run_id not in next_seq:
                    next_seq[event.run_id] = c.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                        (event.run_id,),
                    ).fetchone()[0]
                c.execute(
                    "INSERT OR IGNORE INTO events (event_id, run_id, project_id, seq, type, level,"
                    " scraper, at, schema_version, data_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.run_id,
                        event.project_id,
                        next_seq[event.run_id],
                        event.type,
                        event.level.value,
                        event.scraper,
                        event.at,
                        event.schema_version,
                        dumps(dict(event.data)),
                    ),
                )
                next_seq[event.run_id] += 1

        self._db.write(operation)

    def list(self, run_id: str, *, types: Sequence[str] | None = None) -> list[Event]:
        query = "SELECT * FROM events WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if types:
            query += f" AND type IN ({', '.join('?' for _ in types)})"
            params += tuple(types)
        rows = self._db.read(lambda c: c.execute(query + " ORDER BY seq", params).fetchall())
        return [
            Event(
                event_id=row["event_id"],
                run_id=row["run_id"],
                project_id=row["project_id"],
                type=row["type"],
                at=row["at"],
                level=EventLevel(row["level"]),
                scraper=row["scraper"],
                data=MappingProxyType(loads(row["data_json"])),
                schema_version=row["schema_version"],
            )
            for row in rows
        ]


class SQLiteDeliveries:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, delivery: DeliveryRecord) -> None:
        self._db.write(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO deliveries (delivery_id, run_id, name, kind, status,"
                " attempts, at, location, files_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery.delivery_id,
                    delivery.run_id,
                    delivery.name,
                    delivery.kind,
                    delivery.status.value,
                    delivery.attempts,
                    delivery.at,
                    delivery.location,
                    dumps(list(delivery.files)),
                    delivery.error,
                ),
            )
        )

    def list(self, run_id: str) -> list[DeliveryRecord]:
        rows = self._db.read(
            lambda c: c.execute(
                "SELECT * FROM deliveries WHERE run_id = ? ORDER BY at, delivery_id", (run_id,)
            ).fetchall()
        )
        return [
            DeliveryRecord(
                delivery_id=row["delivery_id"],
                run_id=row["run_id"],
                name=row["name"],
                kind=row["kind"],
                status=DeliveryStatus(row["status"]),
                attempts=row["attempts"],
                at=row["at"],
                location=row["location"],
                files=tuple(loads(row["files_json"])),
                error=row["error"],
            )
            for row in rows
        ]


def _after(iso: str, seconds: float) -> str:
    moment = datetime.fromisoformat(iso) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds")


def _task(row: sqlite3.Row) -> Task:
    return Task(
        task_id=row["task_id"],
        run_id=row["run_id"],
        scraper=row["scraper"],
        kind=TaskKind(row["kind"]),
        payload=MappingProxyType(loads(row["payload_json"])),
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        priority=row["priority"],
        not_before=row["not_before"],
        avoid=tuple(loads(row["avoid_json"])),
        last_category=ErrorCategory(row["last_category"]) if row["last_category"] else None,
        last_error=row["last_error"],
    )


def _attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        attempt_id=row["attempt_id"],
        task_id=row["task_id"],
        run_id=row["run_id"],
        attempt_no=row["attempt_no"],
        slot_id=row["slot_id"],
        host_id=row["host_id"],
        status=AttemptStatus(row["status"]),
        started_at=row["started_at"],
        proxy_id=row["proxy_id"],
        finished_at=row["finished_at"],
        category=ErrorCategory(row["category"]) if row["category"] else None,
        rows=row["rows"],
        error=row["error"],
        details=MappingProxyType(loads(row["details_json"])),
    )


def _key(ref: PartitionRef) -> tuple[str, str, str, int]:
    return ref.run_id, ref.scraper, ref.writer_id, ref.partition_no


def _ref(row: sqlite3.Row) -> PartitionRef:
    return PartitionRef(
        run_id=row["run_id"],
        scraper=row["scraper"],
        writer_id=row["writer_id"],
        partition_no=row["partition_no"],
        path=row["path"],
    )


def _partition(row: sqlite3.Row) -> PartitionState:
    return PartitionState(
        partition=_ref(row),
        committed_bytes=row["committed_bytes"],
        rows=row["rows"],
        sealed=bool(row["sealed"]),
        checksum=row["checksum"],
    )


def _run(row: sqlite3.Row) -> RunRecord:
    summary = loads(row["summary_json"])
    return RunRecord(
        run_id=row["run_id"],
        project_id=row["project_id"],
        status=RunStatus(row["status"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        scrapers=tuple(loads(row["scrapers_json"])),
        packages=MappingProxyType(loads(row["packages_json"])),
        config=MappingProxyType(loads(row["config_json"])),
        summary=MappingProxyType(summary) if summary is not None else None,
        error=row["error"],
        host_id=row["host_id"],
        pid=row["pid"],
    )

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from dailyscraper.domain import (
    BatchCommit,
    CommitRecord,
    DeliveryRecord,
    DeliveryStatus,
    Event,
    EventLevel,
    PartitionRef,
    PartitionSeal,
    PartitionState,
    RunRecord,
    RunStatus,
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
                " scrapers_json, packages_json, config_json, summary_json, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                " start_offset, end_offset, first_record, rows, checksum, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
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
            )
            for row in rows
        ]


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
    )

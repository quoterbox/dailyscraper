from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dailyscraper.domain import PARTITION_RECORD_VERSION, BatchCommit, PartitionRef, PartitionSeal

OK_STATUS = "ok"


class PartitionWriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class ItemIdentity:
    """Stable key of an item across runs: identity fields, else the link, else the content."""

    fields: tuple[str, ...] = ()
    link_field: str | None = None

    def key(self, item: dict[str, Any]) -> str:
        if self.fields:
            values = [str(item.get(name) or "").strip() for name in self.fields]
            if any(values):
                return "|".join(values)
        if self.link_field:
            link = str(item.get(self.link_field) or "").strip()
            if link:
                return normalize_link(link)
        content = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        return "row:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def normalize_link(link: str) -> str:
    parts = urlsplit(link)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


class PartitionWriter:
    """Appends record batches to NDJSON partitions and reports each durable batch.

    A batch is written and synced before ``on_commit`` registers it. When the
    registration fails the bytes are cut off again, so a partition file never
    holds more than its registered size plus, after a crash, an unregistered
    tail that the next writer truncates.
    """

    def __init__(
        self,
        run_root: Path,
        *,
        run_id: str,
        scraper: str,
        writer_id: str,
        on_commit: Callable[[BatchCommit], object],
        on_seal: Callable[[PartitionSeal], object],
        max_rows: int = 100_000,
        max_bytes: int = 128 * 1024 * 1024,
        fsync: bool = True,
        partition_no: int = 1,
        committed_bytes: int = 0,
        committed_rows: int = 0,
        next_record: int = 1,
    ) -> None:
        self.run_root = Path(run_root)
        self.run_id = run_id
        self.scraper = scraper
        self.writer_id = writer_id
        self._on_commit = on_commit
        self._on_seal = on_seal
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.fsync = fsync
        self.partition_no = partition_no
        self.offset = committed_bytes
        self.rows = committed_rows
        self.next_record = next_record

    @property
    def partition(self) -> PartitionRef:
        return PartitionRef(
            run_id=self.run_id,
            scraper=self.scraper,
            writer_id=self.writer_id,
            partition_no=self.partition_no,
            path=f"partitions/{self.scraper}/{self.writer_id}/part-{self.partition_no:06d}.ndjson",
        )

    def append(
        self,
        records: list[dict[str, Any]],
        *,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> BatchCommit | None:
        if not records:
            return None
        numbered = [{**record, "n": self.next_record + i} for i, record in enumerate(records)]
        payload = b"".join(_encode(record) for record in numbered)
        if self.rows and (
            self.rows + len(records) > self.max_rows or self.offset + len(payload) > self.max_bytes
        ):
            self.seal()
        partition = self.partition
        path = self.run_root / partition.path
        self._prepare(path)
        checksum = hashlib.sha256(payload).hexdigest()
        commit = BatchCommit(
            commit_id=_commit_id(partition, self.offset, checksum),
            partition=partition,
            start_offset=self.offset,
            end_offset=self.offset + len(payload),
            first_record=self.next_record,
            rows=len(records),
            checksum=checksum,
            task_id=task_id,
            attempt_id=attempt_id,
            items=tuple(
                (record["input_row"], record.get("status") or OK_STATUS)
                for record in records
                if record.get("input_row") is not None
            ),
        )
        with path.open("ab") as stream:
            stream.write(payload)
            stream.flush()
            if self.fsync:
                os.fsync(stream.fileno())
        try:
            self._on_commit(commit)
        except BaseException:
            truncate(path, commit.start_offset)
            raise
        self.offset = commit.end_offset
        self.rows += len(records)
        self.next_record += len(records)
        return commit

    def seal(self) -> PartitionSeal | None:
        if self.rows == 0:
            return None
        path = self.run_root / self.partition.path
        seal = PartitionSeal(
            partition=self.partition,
            size_bytes=self.offset,
            rows=self.rows,
            checksum=file_checksum(path, self.offset),
        )
        self._on_seal(seal)
        self.partition_no += 1
        self.offset = 0
        self.rows = 0
        return seal

    def _prepare(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        size = path.stat().st_size if path.exists() else 0
        if size < self.offset:
            raise PartitionWriteError(
                f"{path} has {size} bytes, {self.offset} are registered as committed"
            )
        if size > self.offset:
            truncate(path, self.offset)


class PartitionSink:
    """``ItemSink`` that wraps rows into partition records and writes them in batches."""

    def __init__(
        self,
        writer: PartitionWriter,
        *,
        identity: ItemIdentity,
        batch_rows: int = 100,
        batch_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.writer = writer
        self.identity = identity
        self.batch_rows = batch_rows
        self.batch_seconds = batch_seconds
        self._clock = clock
        self._pending: list[dict[str, Any]] = []
        self._first_pending_at = 0.0
        self.statuses: Counter[str] = Counter()
        self.task_id: str | None = None
        self.attempt_id: str | None = None

    @property
    def count(self) -> int:
        return sum(self.statuses.values())

    def start_attempt(self, task_id: str, attempt_id: str) -> None:
        """Later rows belong to this attempt; rows of the previous one are written first."""
        self.flush()
        self.task_id = task_id
        self.attempt_id = attempt_id

    def emit(
        self,
        item: dict[str, Any],
        *,
        input_row: int | None = None,
        status: str | None = None,
        part: int = 0,
    ) -> None:
        status = status or OK_STATUS
        if not self._pending:
            self._first_pending_at = self._clock()
        self._pending.append(
            {
                "v": PARTITION_RECORD_VERSION,
                "key": self.identity.key(item),
                "input_row": input_row,
                "status": status,
                "data": item,
                **({"part": part} if part else {}),
            }
        )
        self.statuses[status] += 1
        if (
            len(self._pending) >= self.batch_rows
            or self._clock() - self._first_pending_at >= self.batch_seconds
        ):
            self.flush()

    def flush(self) -> None:
        if self._pending:
            pending, self._pending = self._pending, []
            self.writer.append(pending, task_id=self.task_id, attempt_id=self.attempt_id)

    def close(self) -> None:
        self.flush()
        self.writer.seal()

    def __enter__(self) -> PartitionSink:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def truncate(path: Path, size: int) -> None:
    if not path.exists():
        return
    with path.open("r+b") as stream:
        stream.truncate(size)
        stream.flush()
        os.fsync(stream.fileno())


def file_checksum(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as stream:
        while remaining > 0:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise PartitionWriteError(f"{path} is shorter than {size} bytes")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _encode(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _commit_id(partition: PartitionRef, start_offset: int, checksum: str) -> str:
    raw = "\0".join(
        (
            partition.run_id,
            partition.scraper,
            partition.writer_id,
            str(partition.partition_no),
            str(start_offset),
            checksum,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

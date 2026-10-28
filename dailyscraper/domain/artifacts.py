from __future__ import annotations

from dataclasses import dataclass

PARTITION_RECORD_VERSION = 1


@dataclass(frozen=True)
class PartitionRef:
    run_id: str
    scraper: str
    writer_id: str
    partition_no: int
    path: str


@dataclass(frozen=True)
class BatchCommit:
    """Rows durably appended to a partition file, waiting to be registered.

    ``path`` is relative to the run directory; ``end_offset`` becomes the
    partition's committed size once the commit is registered.
    """

    commit_id: str
    partition: PartitionRef
    start_offset: int
    end_offset: int
    first_record: int
    rows: int
    checksum: str


@dataclass(frozen=True)
class PartitionSeal:
    partition: PartitionRef
    size_bytes: int
    rows: int
    checksum: str


@dataclass(frozen=True)
class PartitionState:
    partition: PartitionRef
    committed_bytes: int
    rows: int
    sealed: bool
    checksum: str | None = None


@dataclass(frozen=True)
class CommitRecord:
    commit_id: str
    partition: PartitionRef
    start_offset: int
    end_offset: int
    first_record: int
    rows: int
    checksum: str

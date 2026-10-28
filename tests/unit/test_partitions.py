from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.domain import BatchCommit, PartitionSeal
from dailyscraper.execution.partitions import (
    ItemIdentity,
    PartitionSink,
    PartitionWriteError,
    PartitionWriter,
)
from dailyscraper.storage import RunLayout, iter_committed_records
from dailyscraper.storage.records import PartitionIntegrityError
from dailyscraper.storage.sqlite import SQLiteStore


class Recorder:
    def __init__(self) -> None:
        self.commits: list[BatchCommit] = []
        self.seals: list[PartitionSeal] = []
        self.fail_next = False

    def commit(self, batch: BatchCommit) -> bool:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("registry unavailable")
        self.commits.append(batch)
        return True

    def seal(self, seal: PartitionSeal) -> None:
        self.seals.append(seal)


def writer(root: Path, recorder: Recorder, **options) -> PartitionWriter:
    return PartitionWriter(
        root,
        run_id="r1",
        scraper="catalog",
        writer_id="w1",
        on_commit=recorder.commit,
        on_seal=recorder.seal,
        fsync=False,
        **options,
    )


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_batches_are_appended_and_reported(tmp_path: Path) -> None:
    recorder = Recorder()
    partitions = writer(tmp_path, recorder)

    first = partitions.append([{"a": 1}, {"a": 2}])
    second = partitions.append([{"a": 3}])

    assert (first.start_offset, first.end_offset) == (0, second.start_offset)
    assert (first.first_record, second.first_record) == (1, 3)
    path = tmp_path / first.partition.path
    assert [record["n"] for record in lines(path)] == [1, 2, 3]
    assert path.stat().st_size == second.end_offset
    assert [c.commit_id for c in recorder.commits] == [first.commit_id, second.commit_id]


def test_failed_registration_removes_the_written_batch(tmp_path: Path) -> None:
    recorder = Recorder()
    partitions = writer(tmp_path, recorder)
    committed = partitions.append([{"a": 1}])
    recorder.fail_next = True

    with pytest.raises(RuntimeError):
        partitions.append([{"a": 2}])

    path = tmp_path / committed.partition.path
    assert path.stat().st_size == committed.end_offset
    assert partitions.append([{"a": 3}]).start_offset == committed.end_offset


def test_unregistered_tail_is_cut_off_by_the_next_writer(tmp_path: Path) -> None:
    recorder = Recorder()
    committed = writer(tmp_path, recorder).append([{"a": 1}])
    path = tmp_path / committed.partition.path
    with path.open("ab") as stream:
        stream.write(b'{"crashed": true')

    resumed = writer(
        tmp_path, recorder, committed_bytes=committed.end_offset, committed_rows=1, next_record=2
    )
    resumed.append([{"a": 2}])

    assert [record["a"] for record in lines(path)] == [1, 2]


def test_partition_shorter_than_registered_is_an_error(tmp_path: Path) -> None:
    recorder = Recorder()
    committed = writer(tmp_path, recorder).append([{"a": 1}])
    (tmp_path / committed.partition.path).write_bytes(b"")

    with pytest.raises(PartitionWriteError):
        writer(tmp_path, recorder, committed_bytes=committed.end_offset).append([{"a": 2}])


def test_full_partition_is_sealed_and_the_next_one_started(tmp_path: Path) -> None:
    recorder = Recorder()
    partitions = writer(tmp_path, recorder, max_rows=2)

    partitions.append([{"a": 1}, {"a": 2}])
    third = partitions.append([{"a": 3}])

    (seal,) = recorder.seals
    assert (seal.partition.partition_no, seal.rows) == (1, 2)
    assert seal.size_bytes == (tmp_path / seal.partition.path).stat().st_size
    assert (third.partition.partition_no, third.start_offset, third.first_record) == (2, 0, 3)


def test_seal_of_an_empty_writer_does_nothing(tmp_path: Path) -> None:
    recorder = Recorder()

    assert writer(tmp_path, recorder).seal() is None
    assert recorder.seals == []


def test_sink_batches_by_size_and_wraps_records(tmp_path: Path) -> None:
    recorder = Recorder()
    sink = PartitionSink(
        writer(tmp_path, recorder),
        identity=ItemIdentity(link_field="link"),
        batch_rows=2,
        batch_seconds=3600,
    )

    sink.emit({"link": "HTTPS://Example.com/p/1#reviews"})
    assert recorder.commits == []
    sink.emit({"link": "https://example.com/p/2"}, input_row=7, status="not_found")
    assert len(recorder.commits) == 1
    sink.emit({"name": "third"})
    sink.close()

    path = tmp_path / recorder.commits[0].partition.path
    records = lines(path)
    assert [record["key"] for record in records[:2]] == [
        "https://example.com/p/1",
        "https://example.com/p/2",
    ]
    assert records[1]["input_row"] == 7 and records[1]["status"] == "not_found"
    assert records[2]["key"].startswith("row:")
    assert sink.statuses == {"ok": 2, "not_found": 1}
    assert len(recorder.seals) == 1


def test_sink_flushes_an_old_batch_on_the_next_row(tmp_path: Path) -> None:
    recorder = Recorder()
    now = [0.0]
    sink = PartitionSink(
        writer(tmp_path, recorder),
        identity=ItemIdentity(),
        batch_rows=100,
        batch_seconds=5,
        clock=lambda: now[0],
    )

    sink.emit({"a": 1})
    now[0] = 6.0
    sink.emit({"a": 2})

    assert recorder.commits[0].rows == 2


def test_identity_prefers_fields_then_link_then_content() -> None:
    identity = ItemIdentity(fields=("sku",), link_field="link")

    assert identity.key({"sku": " 42 ", "link": "https://a"}) == "42"
    assert identity.key({"sku": "", "link": "https://a/x"}) == "https://a/x"
    assert identity.key({"title": "t"}) == identity.key({"title": "t"})
    assert identity.key({"title": "t"}) != identity.key({"title": "u"})


def test_registered_records_are_read_back_and_verified(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.sqlite")
    layout = RunLayout(tmp_path / "run")
    partitions = PartitionWriter(
        layout.root,
        run_id="r1",
        scraper="catalog",
        writer_id="w1",
        on_commit=store.partitions.commit,
        on_seal=store.partitions.seal,
        fsync=False,
        max_rows=2,
    )
    partitions.append([{"data": 1}, {"data": 2}])
    partitions.append([{"data": 3}])
    tail = partitions.append([{"data": 4}])

    records = list(iter_committed_records(store.partitions, layout, "r1", "catalog"))
    assert [record["data"] for record in records] == [1, 2, 3, 4]

    path = layout.root / tail.partition.path
    path.write_bytes(path.read_bytes().replace(b'"data":4', b'"data":5'))
    with pytest.raises(PartitionIntegrityError):
        list(iter_committed_records(store.partitions, layout, "r1", "catalog"))

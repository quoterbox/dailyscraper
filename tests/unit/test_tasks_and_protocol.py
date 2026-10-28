from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from dailyscraper.browsers import BrowserManager
from dailyscraper.browsers.channel import CommitRejected, SlotChannel, TaskCancelled
from dailyscraper.browsers.messages import (
    Ack,
    Cancel,
    ItemsCommitted,
    Progress,
    ProtocolError,
    RunTask,
    Shutdown,
    commit_ref,
    decode_from_slot,
    decode_to_slot,
    encode,
)
from dailyscraper.domain import (
    AttemptStatus,
    BatchCommit,
    CommitRecord,
    ErrorCategory,
    PartitionRef,
    Task,
    TaskKind,
    TaskOutcome,
    TaskStatus,
)
from dailyscraper.output.merge import latest_attempt_commits
from dailyscraper.schema import Inputs, load_scraper_package
from dailyscraper.settings.models import RuntimeSettings
from dailyscraper.tasks.inputs import create_input_snapshot
from dailyscraper.tasks.planner import PlanningError, plan_tasks
from dailyscraper.tasks.retry import RetryPolicy, Verdict


def task(kind=TaskKind.DETAIL, attempts=1, max_attempts=3, rows=(1, 2, 3)) -> Task:
    return Task(
        task_id="t",
        run_id="r",
        scraper="s",
        kind=kind,
        payload={"rows": list(rows)} if kind is TaskKind.DETAIL else {"link": "https://a"},
        status=TaskStatus.RUNNING,
        attempts=attempts,
        max_attempts=max_attempts,
    )


POLICY = RetryPolicy(base_delay_seconds=5, max_delay_seconds=30)


def test_delay_grows_and_is_capped() -> None:
    assert [POLICY.delay(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 30]


def test_completed_detail_retries_only_its_failed_rows() -> None:
    outcome = TaskOutcome(AttemptStatus.COMPLETED, retry_rows=(3, 2, 3))

    decision = POLICY.decide(task(), outcome)

    assert (decision.verdict, decision.payload) == (Verdict.RETRY, {"rows": [2, 3]})
    assert POLICY.decide(task(), TaskOutcome(AttemptStatus.COMPLETED)).verdict is Verdict.FINISH


def test_crashed_detail_retries_rows_without_a_final_status() -> None:
    outcome = TaskOutcome(AttemptStatus.FAILED, category=ErrorCategory.BROWSER_CLOSED)

    decision = POLICY.decide(task(), outcome, {1: "ok", 2: "timeout"})

    assert decision.payload == {"rows": [2, 3]}


def test_non_retryable_failures_and_exhausted_attempts_finish() -> None:
    filesystem = TaskOutcome(AttemptStatus.FAILED, category=ErrorCategory.FILESYSTEM)
    blocked = TaskOutcome(AttemptStatus.FAILED, category=ErrorCategory.BLOCKED)

    assert POLICY.decide(task(), filesystem).verdict is Verdict.FINISH
    assert POLICY.decide(task(kind=TaskKind.LIST), blocked).verdict is Verdict.RETRY
    assert POLICY.decide(task(kind=TaskKind.LIST, attempts=3), blocked).verdict is Verdict.FINISH
    cancelled = TaskOutcome(AttemptStatus.CANCELLED, category=ErrorCategory.CANCELLED)
    assert POLICY.decide(task(), cancelled).verdict is Verdict.FINISH


def test_planner_makes_list_tasks_per_link_and_detail_chunks(
    project_root: Path, tmp_path: Path
) -> None:
    scrapers = project_root / "tests" / "scrapers"
    catalog = load_scraper_package(scrapers / "controlled_catalog", variables={})
    details = load_scraper_package(scrapers / "controlled_details", variables={})
    source = tmp_path / "in.csv"
    source.write_text(
        "detail_link\n" + "".join(f"https://a/{n}\n" for n in range(5)), encoding="utf-8"
    )
    snapshot = create_input_snapshot(source, tmp_path / "s.csv", Inputs())

    (listing,) = plan_tasks("r1", catalog)
    chunks = plan_tasks("r1", details, snapshot=snapshot, items_per_task=2, max_attempts=4)

    assert (listing.kind, listing.payload) == (
        TaskKind.LIST,
        {"link": "https://dailyscraper.com/tests/"},
    )
    assert [spec.payload["rows"] for spec in chunks] == [[1, 2], [3, 4], [5]]
    assert len({spec.task_id for spec in chunks}) == 3 and chunks[0].max_attempts == 4
    with pytest.raises(PlanningError):
        plan_tasks("r1", details)


def batch() -> BatchCommit:
    return BatchCommit(
        commit_id="c",
        partition=PartitionRef("r", "s", "w", 1, "partitions/s/w/part-000001.ndjson"),
        start_offset=0,
        end_offset=10,
        first_record=1,
        rows=1,
        checksum="x",
        task_id="t",
        attempt_id="t#1",
        items=((4, "ok"),),
    )


def test_messages_round_trip_and_check_the_version() -> None:
    message = ItemsCommitted(commit=batch())
    assert decode_from_slot(encode(message)) == message
    run = RunTask(task_id="t", attempt_id="t#1", kind=TaskKind.LIST, payload={"link": "x"})
    assert decode_to_slot(encode(run)) == run

    with pytest.raises(ProtocolError):
        decode_to_slot(encode(Shutdown(v=99)))


def test_channel_waits_for_its_ack_and_keeps_other_messages() -> None:
    parent, child = multiprocessing.Pipe()
    channel = SlotChannel(child, ack_timeout=5)
    commit = batch()
    parent.send_bytes(encode(Shutdown()))
    parent.send_bytes(encode(Ack(ref=commit_ref(commit), ok=True)))

    channel.commit(commit)

    assert isinstance(decode_from_slot(parent.recv_bytes()), ItemsCommitted)
    assert channel.shutdown_requested
    assert isinstance(channel.receive(timeout=0), Shutdown)


def test_rejected_or_missing_ack_fails_the_commit() -> None:
    parent, child = multiprocessing.Pipe()
    channel = SlotChannel(child, ack_timeout=0.2)
    commit = batch()
    parent.send_bytes(encode(Ack(ref=commit_ref(commit), ok=False, error="conflict")))

    with pytest.raises(CommitRejected, match="conflict"):
        channel.commit(commit)
    with pytest.raises(CommitRejected, match="no acknowledgement"):
        channel.commit(commit)


def test_cancel_interrupts_and_progress_is_reported() -> None:
    parent, child = multiprocessing.Pipe()
    now = [0.0]
    channel = SlotChannel(child, progress_interval=5, clock=lambda: now[0])
    channel.attempt_id = "t#1"

    channel.check_interrupted()
    assert not parent.poll(0)
    now[0] = 6
    channel.check_interrupted()
    assert decode_from_slot(parent.recv_bytes()) == Progress(attempt_id="t#1")

    parent.send_bytes(encode(Cancel()))
    with pytest.raises(TaskCancelled):
        channel.check_interrupted()


def commit(seq_task: str, attempt: str, commit_id: str) -> CommitRecord:
    return CommitRecord(
        commit_id=commit_id,
        partition=PartitionRef("r", "s", "w", 1, "p"),
        start_offset=0,
        end_offset=1,
        first_record=1,
        rows=1,
        checksum="x",
        task_id=seq_task,
        attempt_id=attempt,
    )


def test_list_results_come_from_the_latest_attempt_with_rows() -> None:
    commits = [
        commit("a", "a#1", "a1-partial"),
        commit("b", "b#1", "b1"),
        commit("a", "a#2", "a2"),
        commit(None, None, "legacy"),
    ]

    assert [c.commit_id for c in latest_attempt_commits(commits)] == ["b1", "a2", "legacy"]


class FakeStore:
    class leases:
        @staticmethod
        def acquire(*args, **kwargs):
            return True


def manager(memory_mb: float, **runtime) -> BrowserManager:
    return BrowserManager(
        store=FakeStore(),
        run_id="r",
        host_id="h",
        runtime=RuntimeSettings(**runtime),
        configs=lambda scraper, slot: None,
        available_memory_mb=lambda: memory_mb,
    )


def test_new_slots_need_the_memory_budget_but_the_first_one_always_starts() -> None:
    small = manager(500, memory_reserve_mb=400, slot_memory_mb=300)
    assert small.can_start("s", max_parallel=3)

    small.slots["s-1"] = type("Slot", (), {"scraper": "s", "state": None})()
    assert not small.can_start("s", max_parallel=3)
    assert manager(5000, memory_reserve_mb=400, slot_memory_mb=300).can_start("s", 3)


def test_scraper_and_machine_limits() -> None:
    limited = manager(10_000, max_slots=2)
    limited.slots["a-1"] = type("Slot", (), {"scraper": "a", "state": None})()

    assert not limited.can_start("a", max_parallel=1)
    assert limited.can_start("b", max_parallel=1)
    limited.slots["b-1"] = type("Slot", (), {"scraper": "b", "state": None})()
    assert not limited.can_start("c", max_parallel=1)

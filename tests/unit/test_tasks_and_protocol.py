from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from dailyscraper.browsers import BrowserManager, SlotHandle, SlotState
from dailyscraper.browsers.channel import CommitRejected, SlotChannel, TaskCancelled
from dailyscraper.browsers.messages import (
    Ack,
    Cancel,
    InterventionEnded,
    InterventionStarted,
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
    Dimension,
    ErrorCategory,
    LeaseKey,
    LimitRule,
    PartitionRef,
    Task,
    TaskKind,
    TaskOutcome,
    TaskStatus,
    lease_keys,
)
from dailyscraper.output.merge import latest_attempt_commits
from dailyscraper.schema import Inputs, load_scraper_package
from dailyscraper.settings import Settings
from dailyscraper.settings.models import RuntimeSettings
from dailyscraper.storage.sqlite import SQLiteStore
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

    started = InterventionStarted(
        attempt_id="t#1", kind="captcha", page_type="list", url="x", timeout_seconds=600
    )
    ended = InterventionEnded(attempt_id="t#1", kind="captcha", result="timeout", seconds=600)
    assert decode_from_slot(encode(started)) == started
    assert decode_from_slot(encode(ended)) == ended

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


def manager(store, memory_mb: float = 10_000, site: str = "a.ru", **runtime) -> BrowserManager:
    settings = Settings(
        environment="testing",
        project_root=Path("."),
        paths={"data": ".", "logs": ".", "scrapers": "."},
        runtime=RuntimeSettings(**{"max_browsers_per_site_proxy": 10, **runtime}),
        scrapers=[{"name": "fast", "max_parallel": 3}],
    )
    return BrowserManager(
        store=store,
        run_id="r",
        host_id="h",
        runtime=settings.runtime,
        configs=lambda scraper, slot: None,
        rules=settings.limit_rules(),
        sessions=lambda scraper: {Dimension.SITE: site},
        available_memory_mb=lambda: memory_mb,
    )


def hold(store, browsers: BrowserManager, scraper: str, holder: str) -> None:
    assert (
        store.leases.acquire(holder, browsers.lease_keys(scraper), run_id="r", ttl_seconds=60) == []
    )


def test_new_slots_need_the_memory_budget_but_the_first_one_always_starts(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    small = manager(
        store, 500, memory_reserve_mb=400, slot_memory_mb=300, max_parallel_per_scraper=3
    )
    assert small.can_start("s")

    small.slots["s-1"] = object()
    assert not small.can_start("s")
    roomy = manager(
        store, 5000, memory_reserve_mb=400, slot_memory_mb=300, max_parallel_per_scraper=3
    )
    roomy.slots["s-1"] = object()
    assert roomy.can_start("s")


def test_scraper_parallelism_with_override(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    browsers = manager(store)
    hold(store, browsers, "slow", "slow-1")
    assert not browsers.can_start("slow")

    for number in (1, 2):
        hold(store, browsers, "fast", f"fast-{number}")
    assert browsers.can_start("fast")
    hold(store, browsers, "fast", "fast-3")
    assert not browsers.can_start("fast")


def test_machine_limit_is_shared_by_all_scrapers_and_runs(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    browsers = manager(store, max_slots=2)
    other_run = manager(store, max_slots=2)
    other_run.run_id = "other"
    hold(store, browsers, "a", "a-1")
    hold(store, other_run, "b", "b-1")

    assert not browsers.can_start("c")


def test_site_and_proxy_limit_across_scrapers(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    rule = {"name": "site_proxy", "scope": ["site", "proxy"], "limit": 1}
    browsers = manager(store, max_parallel_per_scraper=5, limits=[rule])
    hold(store, browsers, "ozon_one", "one-1")

    assert not browsers.can_start("ozon_two")
    assert manager(store, site="wb.ru", max_parallel_per_scraper=5, limits=[rule]).can_start("wb")


def test_rules_keys_and_most_specific_override() -> None:
    rules = [
        LimitRule("site_proxy", (Dimension.SITE, Dimension.PROXY), 1),
        LimitRule("site_proxy", (Dimension.SITE, Dimension.PROXY), 4, {Dimension.SITE: "wb.ru"}),
        LimitRule("host", (Dimension.HOST,), 10),
    ]
    session = {Dimension.SITE: "wb.ru", Dimension.PROXY: "p1", Dimension.HOST: "h"}

    assert lease_keys(rules, session) == [
        LeaseKey("site_proxy:site=wb.ru|proxy=p1", 4),
        LeaseKey("host:host=h", 10),
    ]
    assert lease_keys(rules, {**session, Dimension.SITE: "ozon.ru"})[0].limit == 1


def test_one_browser_per_site_and_proxy_by_default(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    browsers = manager(store, max_browsers_per_site_proxy=1, max_parallel_per_scraper=5)
    hold(store, browsers, "ozon_one", "one-1")

    assert not browsers.can_start("ozon_one")
    assert not browsers.can_start("ozon_two")
    assert manager(store, site="wb.ru", max_browsers_per_site_proxy=1).can_start("wb")


class IdleProcess:
    pid = None

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        pass


class ClosedConnection:
    def close(self) -> None:
        pass


def test_waiting_for_a_person_is_not_a_stall_and_does_not_eat_the_deadline(tmp_path) -> None:
    now = [0.0]
    browsers = manager(
        SQLiteStore(tmp_path / "s.sqlite"), task_deadline_seconds=10, stall_timeout_seconds=30
    )
    browsers.clock = lambda: now[0]
    slot = SlotHandle(
        "s-1", "s", IdleProcess(), ClosedConnection(), "h:s-1", 0.0, SlotState.BUSY, "t#1"
    )
    browsers.slots[slot.slot_id] = slot

    def at(moment: float, message=None) -> list:
        now[0] = slot.last_message_at = moment
        if message is not None:
            browsers._track(slot, message)
        return browsers._watchdog()

    started = InterventionStarted(
        attempt_id="t#1", kind="captcha", page_type="list", timeout_seconds=100
    )
    assert at(5, started) == [] and slot.state is SlotState.WAITING
    assert at(50) == []
    ended = InterventionEnded(attempt_id="t#1", kind="captcha", result="resolved", seconds=55)
    assert at(60, ended) == [] and slot.state is SlotState.BUSY
    assert slot.task_started_at == 55

    at(61, started)
    (exited,) = at(192)
    assert exited.reason == "intervention did not end"
    assert exited.category is ErrorCategory.TIMEOUT

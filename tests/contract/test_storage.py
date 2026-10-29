"""Behaviour every storage backend must have; each backend is one ``store`` parameter."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from dailyscraper.domain import (
    AttemptStatus,
    BatchCommit,
    CommandKind,
    DeliveryRecord,
    DeliveryStatus,
    ErrorCategory,
    Event,
    EventLevel,
    LeaseKey,
    Notification,
    NotificationStatus,
    PartitionRef,
    PartitionSeal,
    ProxyHealthState,
    RunRecord,
    RunStatus,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
    Urgency,
)
from dailyscraper.storage import CommitConflict, StorageError, Store, open_store

BACKENDS = ["sqlite"]


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path: Path) -> Store:
    opened = open_store(request.param, tmp_path / "store.sqlite")
    yield opened
    opened.close()


def make_run(run_id: str, *, project_id: str = "default", started_at: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        project_id=project_id,
        status=RunStatus.RUNNING,
        started_at=started_at,
        scrapers=("catalog",),
        packages={"catalog": "abc123"},
        config={"output": {"formats": ["csv"]}},
    )


def ref(partition_no: int = 1, *, run_id: str = "r1", writer_id: str = "w1") -> PartitionRef:
    return PartitionRef(
        run_id=run_id,
        scraper="catalog",
        writer_id=writer_id,
        partition_no=partition_no,
        path=f"partitions/catalog/{writer_id}/part-{partition_no:06d}.ndjson",
    )


def batch(commit_id: str, start: int, end: int, *, partition: PartitionRef | None = None, rows=2):
    return BatchCommit(
        commit_id=commit_id,
        partition=partition or ref(),
        start_offset=start,
        end_offset=end,
        first_record=1,
        rows=rows,
        checksum=f"sum-{commit_id}",
    )


def test_now_is_iso_utc(store: Store) -> None:
    assert datetime.fromisoformat(store.now()).utcoffset().total_seconds() == 0


def test_runs_are_listed_newest_first_and_filtered_by_project(store: Store) -> None:
    store.runs.create(make_run("r1", started_at="2026-10-01T10:00:00+00:00"))
    store.runs.create(make_run("r2", started_at="2026-10-02T10:00:00+00:00"))
    store.runs.create(make_run("r3", project_id="client", started_at="2026-10-03T10:00:00+00:00"))

    assert [run.run_id for run in store.runs.list()] == ["r3", "r2", "r1"]
    assert [run.run_id for run in store.runs.list(project_id="default")] == ["r2", "r1"]
    assert [run.run_id for run in store.runs.list(limit=1)] == ["r3"]


def test_run_round_trip_and_finish(store: Store) -> None:
    store.runs.create(make_run("r1", started_at="2026-10-01T10:00:00+00:00"))
    created = store.runs.get("r1")
    assert created.status is RunStatus.RUNNING
    assert created.packages == {"catalog": "abc123"}
    assert created.config == {"output": {"formats": ["csv"]}}
    assert created.finished_at is None

    finished = store.runs.finish(
        "r1", RunStatus.COMPLETED_WITH_ERRORS, summary={"rows": 3}, error="one link failed"
    )

    assert finished.status is RunStatus.COMPLETED_WITH_ERRORS
    assert finished.summary == {"rows": 3}
    assert finished.error == "one link failed"
    assert finished.finished_at is not None
    assert store.runs.get("missing") is None
    with pytest.raises(StorageError):
        store.runs.finish("missing", RunStatus.FAILED)


def test_commits_extend_the_partition_in_order(store: Store) -> None:
    assert store.partitions.commit(batch("c1", 0, 100)) is True
    assert store.partitions.commit(batch("c2", 100, 250)) is True

    state = store.partitions.state(ref())
    assert (state.committed_bytes, state.rows, state.sealed) == (250, 4, False)
    assert [c.commit_id for c in store.partitions.commits("r1", "catalog")] == ["c1", "c2"]


def test_repeated_commit_is_ignored(store: Store) -> None:
    store.partitions.commit(batch("c1", 0, 100))

    assert store.partitions.commit(batch("c1", 0, 100)) is False
    assert store.partitions.state(ref()).committed_bytes == 100


@pytest.mark.parametrize("start", [0, 50, 150])
def test_commit_must_continue_the_registered_size(store: Store, start: int) -> None:
    store.partitions.commit(batch("c1", 0, 100))

    with pytest.raises(CommitConflict):
        store.partitions.commit(batch("c2", start, start + 10))
    assert store.partitions.state(ref()).committed_bytes == 100


def test_first_commit_must_start_at_zero(store: Store) -> None:
    with pytest.raises(CommitConflict):
        store.partitions.commit(batch("c1", 10, 20))
    assert store.partitions.state(ref()) is None


def test_sealed_partition_accepts_no_commits(store: Store) -> None:
    store.partitions.commit(batch("c1", 0, 100))
    store.partitions.seal(PartitionSeal(ref(), size_bytes=100, rows=2, checksum="full"))

    state = store.partitions.state(ref())
    assert state.sealed and state.checksum == "full"
    with pytest.raises(CommitConflict):
        store.partitions.commit(batch("c2", 100, 200))


def test_seal_must_match_the_registered_size(store: Store) -> None:
    store.partitions.commit(batch("c1", 0, 100))

    with pytest.raises(CommitConflict):
        store.partitions.seal(PartitionSeal(ref(), size_bytes=90, rows=2, checksum="x"))
    with pytest.raises(StorageError):
        store.partitions.seal(PartitionSeal(ref(2), size_bytes=0, rows=0, checksum="x"))


def test_commits_keep_registration_order_across_partitions(store: Store) -> None:
    store.partitions.commit(batch("a", 0, 10, partition=ref(1)))
    store.partitions.commit(batch("b", 0, 10, partition=ref(1, writer_id="w2")))
    store.partitions.commit(batch("c", 10, 20, partition=ref(1)))
    store.partitions.commit(batch("other-run", 0, 10, partition=ref(1, run_id="r2")))

    commits = store.partitions.commits("r1", "catalog")
    assert [c.commit_id for c in commits] == ["a", "b", "c"]
    assert commits[1].partition == ref(1, writer_id="w2")
    assert [p.partition.writer_id for p in store.partitions.partitions("r1")] == ["w1", "w2"]
    assert store.partitions.partitions("r1", "other") == []


def event(event_id: str, type_: str, run_id: str = "r1") -> Event:
    return Event(
        event_id=event_id,
        run_id=run_id,
        project_id="default",
        type=type_,
        at="2026-10-01T10:00:00+00:00",
        level=EventLevel.WARNING,
        scraper="catalog",
        data={"url": "https://example.com", "items": 3},
    )


def test_events_keep_append_order_and_filter_by_type(store: Store) -> None:
    store.events.append([event("e1", "run.started"), event("e2", "link.finished")])
    store.events.append([event("e3", "run.finished"), event("x1", "run.started", run_id="r2")])

    assert [e.event_id for e in store.events.list("r1")] == ["e1", "e2", "e3"]
    assert [e.event_id for e in store.events.list("r1", types=["run.finished"])] == ["e3"]
    stored = store.events.list("r1")[0]
    assert stored.level is EventLevel.WARNING
    assert stored.data == {"url": "https://example.com", "items": 3}


def test_events_are_read_after_a_sequence_number(store: Store) -> None:
    store.events.append([event(f"e{n}", "item.failed") for n in range(1, 6)])

    page = store.events.list("r1", after=2, limit=2)

    assert [(e.event_id, e.seq) for e in page] == [("e3", 3), ("e4", 4)]
    assert store.events.list("r1", after=5) == []


def test_repeated_event_is_stored_once(store: Store) -> None:
    store.events.append([event("e1", "run.started")])
    store.events.append([event("e1", "run.started")])

    assert len(store.events.list("r1")) == 1


def test_deliveries_are_recorded_and_updated(store: Store) -> None:
    record = DeliveryRecord(
        delivery_id="d1",
        run_id="r1",
        name="exports",
        kind="folder",
        status=DeliveryStatus.FAILED,
        attempts=1,
        at="2026-10-01T10:00:00+00:00",
        error="disk full",
    )
    store.deliveries.record(record)
    store.deliveries.record(
        DeliveryRecord(
            **{
                **record.__dict__,
                "status": DeliveryStatus.DELIVERED,
                "attempts": 2,
                "location": "/exports/r1",
                "files": ("catalog.csv",),
                "error": None,
            }
        )
    )

    (stored,) = store.deliveries.list("r1")
    assert stored.status is DeliveryStatus.DELIVERED
    assert (stored.attempts, stored.location, stored.files) == (2, "/exports/r1", ("catalog.csv",))


def detail_task(task_id: str, scraper: str = "catalog", priority: int = 0, attempts: int = 3):
    return TaskSpec(
        task_id=task_id,
        run_id="r1",
        scraper=scraper,
        kind=TaskKind.DETAIL,
        payload={"rows": [1, 2, 3]},
        priority=priority,
        max_attempts=attempts,
    )


def test_claim_takes_tasks_in_priority_and_enqueue_order(store: Store) -> None:
    store.tasks.enqueue([detail_task("a"), detail_task("b", priority=5), detail_task("c")])

    claimed = [store.tasks.claim("r1", scrapers=["catalog"]).task_id for _ in range(3)]

    assert claimed == ["b", "a", "c"]
    assert store.tasks.claim("r1", scrapers=["catalog"]) is None
    assert store.tasks.counts("r1") == {TaskStatus.RUNNING: 3}


def test_claim_prefers_the_least_busy_scraper(store: Store) -> None:
    store.tasks.enqueue([detail_task("a1", "a"), detail_task("a2", "a"), detail_task("b1", "b")])
    store.tasks.claim("r1", scrapers=["a", "b"])

    assert store.tasks.claim("r1", scrapers=["a", "b"]).scraper == "b"
    assert store.tasks.claim("r1", scrapers=["b"]) is None


def test_attempt_lifecycle_and_final_status(store: Store) -> None:
    store.tasks.enqueue([detail_task("t")])
    store.tasks.claim("r1", scrapers=["catalog"])
    attempt = store.tasks.start("t", slot_id="catalog-1", host_id="h1")

    assert (attempt.attempt_no, attempt.status) == (1, AttemptStatus.RUNNING)
    task = store.tasks.finish(
        attempt.attempt_id,
        TaskOutcome(AttemptStatus.COMPLETED, rows=3, details={"pages": [3]}),
    )

    assert (task.status, task.attempts) == (TaskStatus.COMPLETED, 1)
    (stored,) = store.tasks.attempts("r1", task_id="t")
    assert (stored.status, stored.rows, stored.details) == (
        AttemptStatus.COMPLETED,
        3,
        {"pages": [3]},
    )
    assert stored.finished_at is not None


def test_failed_attempt_makes_the_task_failed(store: Store) -> None:
    store.tasks.enqueue([detail_task("t")])
    store.tasks.claim("r1", scrapers=["catalog"])
    attempt = store.tasks.start("t", slot_id="s", host_id="h")

    task = store.tasks.finish(
        attempt.attempt_id,
        TaskOutcome(AttemptStatus.FAILED, category=ErrorCategory.BLOCKED, error="403"),
    )

    assert task.status is TaskStatus.FAILED
    assert (task.last_category, task.last_error) == (ErrorCategory.BLOCKED, "403")


def test_rescheduled_task_waits_until_due_with_new_payload(store: Store) -> None:
    store.tasks.enqueue([detail_task("t")])
    store.tasks.claim("r1", scrapers=["catalog"])
    first = store.tasks.start("t", slot_id="s", host_id="h")

    task = store.tasks.reschedule(
        first.attempt_id,
        TaskOutcome(AttemptStatus.FAILED, category=ErrorCategory.TIMEOUT),
        not_before="9999-01-01T00:00:00.000+00:00",
        payload={"rows": [3]},
        avoid=["proxy-1"],
    )

    assert (task.status, task.payload, task.avoid) == (
        TaskStatus.PENDING,
        {"rows": [3]},
        ("proxy-1",),
    )
    assert store.tasks.claim("r1", scrapers=["catalog"]) is None

    store.tasks.enqueue([detail_task("due")])
    store.tasks.claim("r1", scrapers=["catalog"])
    second = store.tasks.start("due", slot_id="s", host_id="h")
    store.tasks.reschedule(
        second.attempt_id,
        TaskOutcome(AttemptStatus.FAILED),
        not_before="2000-01-01T00:00:00.000+00:00",
    )
    retried = store.tasks.claim("r1", scrapers=["catalog"])
    assert retried.task_id == "due"
    assert store.tasks.start("due", slot_id="s", host_id="h").attempt_no == 2


def test_closing_an_attempt_twice_changes_nothing(store: Store) -> None:
    store.tasks.enqueue([detail_task("t")])
    store.tasks.claim("r1", scrapers=["catalog"])
    attempt = store.tasks.start("t", slot_id="s", host_id="h")
    store.tasks.finish(attempt.attempt_id, TaskOutcome(AttemptStatus.COMPLETED))

    task = store.tasks.finish(attempt.attempt_id, TaskOutcome(AttemptStatus.FAILED))

    assert task.status is TaskStatus.COMPLETED


def test_released_and_cancelled_tasks(store: Store) -> None:
    store.tasks.enqueue([detail_task("a"), detail_task("b")])
    claimed = store.tasks.claim("r1", scrapers=["catalog"])
    with pytest.raises(StorageError):
        store.tasks.start("b", slot_id="s", host_id="h")

    store.tasks.release(claimed.task_id)
    assert store.tasks.counts("r1") == {TaskStatus.PENDING: 2}
    assert store.tasks.cancel_pending("r1") == 2
    assert [task.status for task in store.tasks.list("r1")] == [TaskStatus.CANCELLED] * 2


def test_claim_can_be_limited_to_task_kinds(store: Store) -> None:
    probe = TaskSpec(
        task_id="p", run_id="r1", scraper="catalog", kind=TaskKind.PROBE, payload={"links": []}
    )
    store.tasks.enqueue([detail_task("d"), probe])

    assert store.tasks.claim("r1", scrapers=["catalog"], kinds=[TaskKind.LIST]) is None
    assert store.tasks.claim("r1", scrapers=["catalog"], kinds=[]) is None
    assert store.tasks.claim("r1", scrapers=["catalog"], kinds=[TaskKind.PROBE]).task_id == "p"
    assert store.tasks.claim("r1", scrapers=["catalog"]).task_id == "d"


def test_pending_tasks_of_one_scraper_are_cancelled(store: Store) -> None:
    store.tasks.enqueue([detail_task("a"), detail_task("b", scraper="other")])

    assert store.tasks.cancel_pending("r1", scraper="other") == 1
    statuses = {task.task_id: task.status for task in store.tasks.list("r1")}
    assert statuses == {"a": TaskStatus.PENDING, "b": TaskStatus.CANCELLED}


def test_item_statuses_follow_the_latest_commit(store: Store) -> None:
    def detail_batch(commit_id, start, end, attempt_id, items):
        return BatchCommit(
            commit_id=commit_id,
            partition=ref(),
            start_offset=start,
            end_offset=end,
            first_record=1,
            rows=len(items),
            checksum="x",
            task_id="t",
            attempt_id=attempt_id,
            items=items,
        )

    store.partitions.commit(detail_batch("c1", 0, 10, "t#1", ((1, "ok"), (2, "timeout"))))
    store.partitions.commit(detail_batch("c2", 10, 20, "t#2", ((2, "ok"),)))

    assert store.partitions.item_statuses("r1", "catalog") == {1: "ok", 2: "ok"}
    assert store.partitions.item_statuses("r1", "catalog", attempt_id="t#1") == {1: "ok"}
    commits = store.partitions.commits("r1", "catalog")
    assert [(c.task_id, c.attempt_id) for c in commits] == [("t", "t#1"), ("t", "t#2")]


def test_commands_are_pending_until_applied(store: Store) -> None:
    stop = store.commands.issue("r1", CommandKind.STOP)
    store.commands.issue("r2", CommandKind.KILL)

    assert [c.command_id for c in store.commands.pending("r1")] == [stop.command_id]
    store.commands.mark_applied(stop.command_id)
    assert store.commands.pending("r1") == []


HOST = LeaseKey("host:host=h1", 2)
SITE_PROXY = LeaseKey("site_proxy:site=a.ru|proxy=p1", 1)


def acquire(store: Store, holder: str, *keys: LeaseKey, ttl: float = 60.0) -> list[str]:
    return store.leases.acquire(holder, keys, run_id="r1", ttl_seconds=ttl)


def test_leases_limit_holders_per_key(store: Store) -> None:
    assert acquire(store, "s1", HOST) == [] and acquire(store, "s2", HOST) == []

    assert acquire(store, "s3", HOST) == [HOST.key]
    assert acquire(store, "s1", HOST) == []
    assert store.leases.count(HOST.key) == 2
    assert store.leases.blocked([HOST]) == [HOST.key]

    store.leases.release("s1")
    assert store.leases.blocked([HOST]) == []
    assert acquire(store, "s3", HOST) == []


def test_all_keys_are_taken_or_none(store: Store) -> None:
    assert acquire(store, "s1", SITE_PROXY) == []

    assert acquire(store, "s2", HOST, SITE_PROXY) == [SITE_PROXY.key]
    assert store.leases.count(HOST.key) == 0

    other_proxy = LeaseKey("site_proxy:site=a.ru|proxy=p2", 1)
    assert acquire(store, "s2", HOST, other_proxy) == []
    assert store.leases.count(HOST.key) == 1


def test_expired_leases_free_their_place(store: Store) -> None:
    assert acquire(store, "s1", SITE_PROXY, ttl=-1) == []

    assert store.leases.count(SITE_PROXY.key) == 0
    assert acquire(store, "s2", SITE_PROXY) == []


def test_renewed_leases_stay(store: Store) -> None:
    acquire(store, "s1", SITE_PROXY, ttl=-1)
    store.leases.renew(["s1"], ttl_seconds=60)

    assert acquire(store, "s2", SITE_PROXY) == [SITE_PROXY.key]


def test_proxy_health_starts_fresh_and_updates_atomically(store: Store) -> None:
    fresh = store.proxy_health.states("a.ru", ["p1", "p2"])
    assert fresh["p1"] == ProxyHealthState("a.ru", "p1")

    def fail(state: ProxyHealthState, now: str) -> ProxyHealthState:
        return replace(state, failures=state.failures + 1, updated_at=now)

    before, after = store.proxy_health.update("a.ru", "p1", fail)
    _, again = store.proxy_health.update("a.ru", "p1", fail)

    assert (before.failures, after.failures, again.failures) == (0, 1, 2)
    assert after.updated_at is not None
    assert store.proxy_health.states("a.ru", ["p1"])["p1"].failures == 2
    assert store.proxy_health.states("b.ru", ["p1"])["p1"].failures == 0


def notification(
    notification_id: str,
    *,
    kind: str = "intervention_required",
    channel: str = "telegram",
    urgency: Urgency = Urgency.URGENT,
    cooldown: float = 900,
    ttl: float = 900,
) -> Notification:
    return Notification(
        notification_id=notification_id,
        run_id="r1",
        kind=kind,
        coalesce_key=f"{kind}:r1",
        channel=channel,
        urgency=urgency,
        title="CAPTCHA",
        body=notification_id,
        cooldown_seconds=cooldown,
        ttl_seconds=ttl,
    )


def test_notifications_and_cursor_are_stored_together(store: Store) -> None:
    counts = store.notifications.enqueue([notification("n1")], consumer="c", cursor=4)
    again = store.notifications.enqueue([notification("n1")], consumer="c", cursor=2)

    assert counts == {"created": 1, "merged": 0, "suppressed": 0}
    assert again["merged"] == 1
    assert store.notifications.cursor("c") == 4
    assert store.notifications.cursor("other") == 0
    (record,) = store.notifications.due()
    assert (record.status, record.attempts, record.event_count) == (
        NotificationStatus.PENDING,
        0,
        1,
    )


def test_a_pending_message_absorbs_repeats_per_channel(store: Store) -> None:
    store.notifications.enqueue(
        [notification("n1"), notification("n2"), notification("n3", channel="email:a@b.c")],
        consumer="c",
        cursor=1,
    )

    records = store.notifications.list("r1")
    assert [(r.notification_id, r.event_count) for r in records] == [("n1", 2), ("n3", 1)]
    assert records[0].body == "n1"


def test_a_repeat_within_the_cooldown_after_sending_is_suppressed(store: Store) -> None:
    store.notifications.enqueue([notification("n1")], consumer="c", cursor=1)
    store.notifications.mark_sent("n1")

    counts = store.notifications.enqueue([notification("n2")], consumer="c", cursor=2)
    later = store.notifications.enqueue([notification("n3", cooldown=0)], consumer="c", cursor=3)

    assert counts["suppressed"] == 1 and later["created"] == 1
    sent = store.notifications.list("r1")[0]
    assert (sent.status, sent.suppressed_count, sent.attempts) == (NotificationStatus.SENT, 1, 1)
    assert sent.sent_at is not None


def test_due_messages_go_urgent_first_and_wait_after_a_retry(store: Store) -> None:
    store.notifications.enqueue(
        [
            notification("n1", kind="run_finished", urgency=Urgency.NORMAL),
            notification("n2", kind="run_failed"),
            notification("n3", kind="run_started", urgency=Urgency.NORMAL),
        ],
        consumer="c",
        cursor=3,
    )
    store.notifications.reschedule("n3", delay_seconds=60, error="429 too many requests")

    assert [r.notification_id for r in store.notifications.due()] == ["n2", "n1"]
    assert [r.notification_id for r in store.notifications.due(run_id="r2")] == []
    retried = store.notifications.list("r1")[2]
    assert (retried.attempts, retried.last_error) == (1, "429 too many requests")


def test_failed_and_expired_messages_are_not_due(store: Store) -> None:
    store.notifications.enqueue(
        [notification("n1"), notification("n2", kind="run_failed", ttl=0.001)],
        consumer="c",
        cursor=2,
    )
    store.notifications.mark_failed("n1", error="403 chat not found")
    time.sleep(0.01)

    assert store.notifications.expire() == 1
    assert store.notifications.due() == []
    statuses = [r.status for r in store.notifications.list("r1")]
    assert statuses == [NotificationStatus.FAILED, NotificationStatus.EXPIRED]

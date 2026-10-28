"""Runs through real slot processes with the browserless pipeline from ``tests.fakes``."""

from __future__ import annotations

import threading
import time

import pytest

from dailyscraper.app import cli
from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import DeliveryStatus, RunRecord, RunStatus, TaskStatus
from dailyscraper.settings.models import (
    DeliverySettings,
    LimitSettings,
    OutputSettings,
    ScraperSelection,
)
from tests.fakes import OPENER
from tests.integration.runs import make_input, make_package, output_rows, run

pytestmark = pytest.mark.serial


def test_list_tasks_run_in_slots_and_are_exported(settings, store, project_root) -> None:
    name = make_package(settings, project_root, "catalog", "list", {"rows": 3})

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    assert len(output_rows(report, name)) == 6
    summary = report.run.summary["scrapers"][name]
    assert summary["tasks"] == {"completed": 2}
    assert [link["items"] for link in summary["links"]] == [3, 3]
    assert summary["statuses"] == {"ok": 6}


def test_detail_chunks_run_in_parallel_and_keep_input_order(
    settings, store, project_root, scenario_workspace
) -> None:
    name = make_package(settings, project_root, "details", "detail", {"row_delay": 0.2})
    settings = settings.model_copy(
        update={"scrapers": (ScraperSelection(name=name, max_parallel=3),)}
    )
    source = make_input(scenario_workspace / "in.csv", 10)

    report = run(settings, store, name, input_path=source)

    assert report.run.status is RunStatus.COMPLETED
    rows = output_rows(report, name)
    assert [row["source_id"] for row in rows] == [f"s{n}" for n in range(1, 11)]
    assert all(row["title"] == f"title {n}" for n, row in enumerate(rows, start=1))
    assert report.run.summary["slots"] == 3
    assert report.run.summary["scrapers"][name]["tasks"] == {"completed": 5}


def test_failed_detail_rows_are_retried_alone(
    settings, store, project_root, scenario_workspace
) -> None:
    fake = {"attempts": {"1": {"statuses": {"2": "timeout"}}}}
    name = make_package(settings, project_root, "details", "detail", fake)

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 2))

    assert report.run.status is RunStatus.COMPLETED
    assert store.partitions.item_statuses(report.run.run_id, name) == {1: "ok", 2: "ok"}
    (task,) = store.tasks.list(report.run.run_id)
    assert task.attempts == 2 and task.payload == {"rows": [2]}


def test_crashed_slot_is_replaced_and_partial_rows_are_not_duplicated(
    settings, store, project_root
) -> None:
    fake = {"rows": 3, "attempts": {"1": {"crash_after": 2}}}
    name = make_package(settings, project_root, "catalog", "list", fake)
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"items_per_task": 50})}
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    assert len(output_rows(report, name)) == 6
    attempts = store.tasks.attempts(report.run.run_id)
    crashed = [attempt for attempt in attempts if attempt.category is not None]
    assert crashed and all(attempt.category.value == "browser_closed" for attempt in crashed)


def test_persistent_link_failure_completes_with_errors(settings, store, project_root) -> None:
    name = make_package(
        settings, project_root, "catalog", "list", {"rows": 0, "category": "blocked"}
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    tasks = store.tasks.list(report.run.run_id)
    assert {task.status for task in tasks} == {TaskStatus.FAILED}
    assert all(task.attempts == settings.runtime.task_attempts for task in tasks)
    failures = store.events.list(report.run.run_id, types=["item.failed"])
    assert failures and failures[0].data["category"] == "blocked"


def test_stalled_slot_is_killed_by_the_watchdog(settings, store, project_root) -> None:
    fake = {"rows": 1, "attempts": {"1": {"hang": True}}}
    name = make_package(settings, project_root, "catalog", "list", fake)
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"stall_timeout_seconds": 3})}
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    categories = {attempt.category for attempt in store.tasks.attempts(report.run.run_id)}
    assert any(category is not None and category.value == "timeout" for category in categories)


def test_one_slot_per_machine_limits_parallelism(
    settings, store, project_root, scenario_workspace
) -> None:
    name = make_package(settings, project_root, "details", "detail", {})
    settings = settings.model_copy(
        update={
            "scrapers": (ScraperSelection(name=name, max_parallel=3),),
            "runtime": settings.runtime.model_copy(update={"max_slots": 1}),
        }
    )

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 6))

    assert report.run.status is RunStatus.COMPLETED
    assert report.run.summary["slots"] == 1


def test_stop_command_cancels_the_rest_and_keeps_saved_rows(
    settings, store, project_root, scenario_workspace
) -> None:
    name = make_package(settings, project_root, "details", "detail", {"row_delay": 0.5})
    service = RunService(settings, store, opener=OPENER)

    def stop_when_started() -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            runs = store.runs.list(limit=1)
            if runs and store.partitions.item_statuses(runs[0].run_id, name):
                service.stop(runs[0].run_id)
                return
            time.sleep(0.2)

    stopper = threading.Thread(target=stop_when_started)
    stopper.start()
    report = service.run(RunRequest((name,), make_input(scenario_workspace / "in.csv", 20)))
    stopper.join()

    assert report.run.status is RunStatus.INTERRUPTED
    assert "stop command" in report.run.error
    counts = store.tasks.counts(report.run.run_id)
    assert counts.get(TaskStatus.CANCELLED, 0) > 0
    rows = output_rows(report, name)
    assert len(rows) == 20
    assert any(row.get("title") for row in rows)


def test_pipeline_error_is_retried_then_reported(settings, store, project_root) -> None:
    name = make_package(settings, project_root, "catalog", "list", {"raise": "boom"})

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    attempts = store.tasks.attempts(report.run.run_id)
    assert all(attempt.category.value == "internal" for attempt in attempts)
    assert "boom" in attempts[0].error


def test_delivery_and_history_after_a_run(settings, store, project_root, monkeypatch, capsys):
    name = make_package(settings, project_root, "catalog", "list", {"rows": 2})
    target = settings.paths.data / "exports"
    settings = settings.model_copy(
        update={
            "output": OutputSettings(
                formats=("csv",),
                deliveries=(DeliverySettings(name="folder", type="folder", path=str(target)),),
            )
        }
    )

    report = run(settings, store, name)

    assert [d.status for d in report.deliveries] == [DeliveryStatus.DELIVERED]
    assert (target / f"{name}.csv").is_file()
    monkeypatch.setattr(cli, "load_settings", lambda root: settings)
    assert cli.main(["runs"]) == 0
    assert report.run.run_id in capsys.readouterr().out
    assert cli.main(["runs", "show", report.run.run_id]) == 0
    assert '"completed": 2' in capsys.readouterr().out


def test_orphaned_run_is_closed(settings, store) -> None:
    service = RunService(settings, store, opener=OPENER)
    store.runs.create(
        RunRecord(
            run_id="orphan",
            project_id="default",
            status=RunStatus.RUNNING,
            started_at=store.now(),
            scrapers=("x",),
            host_id=service.host_id,
            pid=999_999_999,
        )
    )

    assert service.reconcile_orphans() == ["orphan"]
    assert store.runs.get("orphan").status is RunStatus.INTERRUPTED


def test_missing_products_do_not_spoil_the_run(
    settings, store, project_root, scenario_workspace
) -> None:
    name = make_package(
        settings, project_root, "details", "detail", {"statuses": {"2": "not_found"}}
    )

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 3))

    assert report.run.status is RunStatus.COMPLETED
    assert report.run.summary["scrapers"][name]["statuses"] == {"ok": 2, "not_found": 1}
    assert [e.level.value for e in store.events.list(report.run.run_id, types=["item.failed"])] == [
        "info"
    ]


def test_site_and_proxy_limit_caps_slots(settings, store, project_root, scenario_workspace) -> None:
    name = make_package(settings, project_root, "details", "detail", {})
    rule = {"name": "site_proxy", "scope": ["site", "proxy"], "limit": 1}
    settings = settings.model_copy(
        update={
            "scrapers": (ScraperSelection(name=name, max_parallel=3),),
            "runtime": settings.runtime.model_copy(
                update={"limits": (LimitSettings.model_validate(rule),)}
            ),
        }
    )

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 6))

    assert report.run.status is RunStatus.COMPLETED
    assert report.run.summary["slots"] == 1

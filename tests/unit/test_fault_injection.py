from __future__ import annotations

import errno
import json
import sqlite3
from pathlib import Path

import pytest

from dailyscraper.settings.environment import ENVIRONMENT_VARIABLE, TEST_WORKSPACE_VARIABLE
from dailyscraper.testing.fault_injection import (
    FAULT_PLAN_VARIABLE,
    NULL_FAULT_INJECTOR,
    FaultPlan,
    FaultPlanError,
    FaultPoint,
    InjectedBrowserClose,
    InjectedHttpStatus,
    InjectedNavigationTimeout,
    PlannedFaultInjector,
    get_fault_injector,
    read_fault_events,
    reset_fault_injector_cache,
)
from dailyscraper.testing.fault_injection.filesystem import hit_filesystem_fault


def test_fault_injector_is_noop_without_explicit_plan(monkeypatch) -> None:
    monkeypatch.delenv(FAULT_PLAN_VARIABLE, raising=False)

    assert get_fault_injector() is NULL_FAULT_INJECTOR


def test_fault_plan_is_rejected_outside_testing_environment(tmp_path: Path, monkeypatch) -> None:
    plan = _write_plan(tmp_path / "fault-plan.json", [])
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "development")
    monkeypatch.setenv(TEST_WORKSPACE_VARIABLE, str(tmp_path))
    monkeypatch.setenv(FAULT_PLAN_VARIABLE, str(plan))

    with pytest.raises(FaultPlanError, match="allowed only"):
        get_fault_injector()


def test_fault_plan_must_be_inside_testing_workspace(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = _write_plan(tmp_path / "outside.json", [])
    monkeypatch.setenv(TEST_WORKSPACE_VARIABLE, str(workspace))
    monkeypatch.setenv(FAULT_PLAN_VARIABLE, str(plan))

    with pytest.raises(FaultPlanError, match="inside"):
        get_fault_injector()


def test_navigation_timeout_is_injected_once_across_injector_instances(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path / "fault-plan.json",
        [
            {
                "id": "first-catalog-navigation",
                "point": "navigation.before_open",
                "fault": "navigation_timeout",
                "match": {"scraper": "controlled_catalog", "navigation_attempt": 1},
            }
        ],
    )
    plan = FaultPlan.load(plan_path)
    first = PlannedFaultInjector(plan)

    first.hit(FaultPoint.NAVIGATION_BEFORE_OPEN, scraper="another_scraper", navigation_attempt=1)
    with pytest.raises(InjectedNavigationTimeout, match="first-catalog-navigation"):
        first.hit(
            FaultPoint.NAVIGATION_BEFORE_OPEN,
            scraper="controlled_catalog",
            navigation_attempt=1,
        )

    PlannedFaultInjector(plan).hit(
        FaultPoint.NAVIGATION_BEFORE_OPEN,
        scraper="controlled_catalog",
        navigation_attempt=1,
    )
    events = read_fault_events(plan_path)
    assert len(events) == 1
    assert events[0]["fault"] == "navigation_timeout"
    assert events[0]["activation"] == 1
    assert events[0]["context"]["scraper"] == "controlled_catalog"


def test_fault_occurrence_and_bounded_delay_are_deterministic(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path / "delay-plan.json",
        [
            {
                "id": "second-navigation",
                "point": "navigation.before_open",
                "fault": "delay",
                "occurrence": 2,
                "parameters": {"seconds": 1.5},
            }
        ],
    )
    sleeps: list[float] = []
    injector = PlannedFaultInjector(FaultPlan.load(plan_path), sleep_fn=sleeps.append)

    injector.hit("navigation.before_open")
    assert sleeps == []
    injector.hit("navigation.before_open")
    injector.hit("navigation.before_open")

    assert sleeps == [1.5]
    assert len(read_fault_events(plan_path)) == 1


def test_browser_close_targets_one_task_kind_after_session_open(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path / "browser-close-plan.json",
        [
            {
                "id": "close-first-probe-session",
                "point": "slot.after_session_open",
                "fault": "browser_close",
                "match": {"task_kind": "probe"},
            }
        ],
    )
    injector = PlannedFaultInjector(FaultPlan.load(plan_path))

    injector.hit(FaultPoint.SLOT_AFTER_SESSION_OPEN, task_kind="list")
    with pytest.raises(InjectedBrowserClose, match="browser has been closed"):
        injector.hit(FaultPoint.SLOT_AFTER_SESSION_OPEN, task_kind="probe")

    assert len(read_fault_events(plan_path)) == 1


def test_http_status_fault_carries_status_code(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path / "http-status-plan.json",
        [
            {
                "id": "rate-limit-first-proxy",
                "point": "navigation.after_open",
                "fault": "http_status",
                "match": {"proxy": "synthetic_proxy_1"},
                "parameters": {"status_code": 429},
            }
        ],
    )
    injector = PlannedFaultInjector(FaultPlan.load(plan_path))

    with pytest.raises(InjectedHttpStatus) as captured:
        injector.hit(FaultPoint.NAVIGATION_AFTER_OPEN, proxy="synthetic_proxy_1")

    assert captured.value.status_code == 429
    events = read_fault_events(plan_path)
    assert events[0]["parameters"] == {"status_code": 429}


def test_slot_crash_uses_configured_process_exit_after_recording_event(tmp_path: Path) -> None:
    plan_path = _write_plan(
        tmp_path / "slot-crash-plan.json",
        [
            {
                "id": "crash-first-catalog-slot",
                "point": "slot.after_task_started",
                "fault": "slot_crash",
                "match": {"scraper": "controlled_catalog", "attempt": 1},
                "parameters": {"exit_code": 73},
            }
        ],
    )
    exit_codes: list[int] = []
    injector = PlannedFaultInjector(FaultPlan.load(plan_path), exit_fn=exit_codes.append)

    with pytest.raises(RuntimeError, match="unexpectedly returned"):
        injector.hit(
            FaultPoint.SLOT_AFTER_TASK_STARTED,
            scraper="controlled_catalog",
            attempt=1,
            task_id="catalog-task-1",
        )

    assert exit_codes == [73]
    events = read_fault_events(plan_path)
    assert len(events) == 1
    assert events[0]["fault"] == "slot_crash"
    assert events[0]["context"]["task_id"] == "catalog-task-1"


@pytest.mark.parametrize(
    ("fault", "parameters", "error_type", "error_code"),
    [
        ("filesystem_permission_error", {}, PermissionError, errno.EACCES),
        ("filesystem_os_error", {"errno": errno.EBUSY}, OSError, errno.EBUSY),
    ],
)
def test_filesystem_faults_raise_portable_os_errors_with_evidence(
    tmp_path: Path,
    monkeypatch,
    fault: str,
    parameters: dict,
    error_type,
    error_code: int,
) -> None:
    plan_path = _write_plan(
        tmp_path / f"{fault}.json",
        [
            {
                "id": f"inject-{fault}",
                "point": "filesystem.output.before_replace",
                "fault": fault,
                "match": {"path_prefix": "main"},
                "parameters": parameters,
            }
        ],
    )
    monkeypatch.setenv(TEST_WORKSPACE_VARIABLE, str(tmp_path))
    monkeypatch.setenv(FAULT_PLAN_VARIABLE, str(plan_path))
    reset_fault_injector_cache()

    with pytest.raises(error_type) as captured:
        hit_filesystem_fault(
            FaultPoint.FILESYSTEM_OUTPUT_BEFORE_REPLACE,
            tmp_path / "main__catalog.csv",
            scope="required_output",
        )
    reset_fault_injector_cache()

    assert captured.value.errno == error_code
    events = read_fault_events(plan_path)
    assert len(events) == 1
    assert events[0]["context"]["path_name"] == "main__catalog.csv"
    assert events[0]["context"]["path_suffix"] == ".csv"


@pytest.mark.parametrize(
    ("fault", "message"),
    [("sqlite_busy", "database is busy"), ("sqlite_locked", "database is locked")],
)
def test_sqlite_faults_raise_operational_errors_with_evidence(
    tmp_path: Path,
    fault: str,
    message: str,
) -> None:
    plan_path = _write_plan(
        tmp_path / f"{fault}.json",
        [
            {
                "id": f"inject-{fault}",
                "point": "sqlite.before_operation",
                "fault": fault,
                "match": {"statement_kind": "update", "table": "tasks"},
            }
        ],
    )
    injector = PlannedFaultInjector(FaultPlan.load(plan_path))

    with pytest.raises(sqlite3.OperationalError, match=message):
        injector.hit(
            FaultPoint.SQLITE_BEFORE_OPERATION,
            database_name="runtime.sqlite",
            operation="execute",
            statement_kind="update",
            table="tasks",
            retry_attempt=1,
            read_only=False,
        )

    events = read_fault_events(plan_path)
    assert len(events) == 1
    assert events[0]["context"]["table"] == "tasks"


@pytest.mark.parametrize(
    "fault",
    [
        {"id": "bad point", "point": "navigation.before_open", "fault": "navigation_timeout"},
        {"id": "unknown-fault", "point": "navigation.before_open", "fault": "browser_explosion"},
        {"id": "unknown-point", "point": "worker.after_job_started", "fault": "delay"},
        {
            "id": "unsafe-delay",
            "point": "navigation.before_open",
            "fault": "delay",
            "parameters": {"seconds": 0},
        },
        {
            "id": "close-without-session",
            "point": "navigation.before_open",
            "fault": "browser_close",
        },
        {
            "id": "successful-http-is-not-a-fault",
            "point": "navigation.after_open",
            "fault": "http_status",
            "parameters": {"status_code": 200},
        },
        {"id": "crash-at-navigation", "point": "navigation.before_open", "fault": "slot_crash"},
        {
            "id": "crash-success-exit",
            "point": "slot.after_task_started",
            "fault": "slot_crash",
            "parameters": {"exit_code": 0},
        },
        {
            "id": "filesystem-at-navigation",
            "point": "navigation.before_open",
            "fault": "filesystem_permission_error",
        },
        {
            "id": "filesystem-non-transient-errno",
            "point": "filesystem.required.before_replace",
            "fault": "filesystem_os_error",
            "parameters": {"errno": 2},
        },
        {"id": "sqlite-at-navigation", "point": "navigation.before_open", "fault": "sqlite_busy"},
    ],
)
def test_invalid_fault_rules_fail_closed(tmp_path: Path, fault: dict) -> None:
    plan_path = _write_plan(tmp_path / "invalid.json", [fault])

    with pytest.raises(FaultPlanError):
        FaultPlan.load(plan_path)


def test_cached_fault_plan_reloads_after_file_change(tmp_path: Path, monkeypatch) -> None:
    plan_path = _write_plan(tmp_path / "fault-plan.json", [])
    monkeypatch.setenv(TEST_WORKSPACE_VARIABLE, str(tmp_path))
    monkeypatch.setenv(FAULT_PLAN_VARIABLE, str(plan_path))
    reset_fault_injector_cache()

    first = get_fault_injector()
    _write_plan(
        plan_path,
        [{"id": "new-rule", "point": "navigation.before_open", "fault": "navigation_timeout"}],
    )
    second = get_fault_injector()
    reset_fault_injector_cache()

    assert first is not second
    assert len(second.plan.rules) == 1


def _write_plan(path: Path, faults: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "faults": faults}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path

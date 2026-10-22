from __future__ import annotations

import errno
import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from dailyscraper.settings.environment import (
    TEST_WORKSPACE_VARIABLE,
    TESTING_ENVIRONMENT,
    current_environment,
)

from .models import (
    FaultPlan,
    FaultPlanError,
    FaultPoint,
    FaultRule,
    FaultScalar,
    FaultType,
    InjectedBrowserClose,
    InjectedHttpStatus,
    InjectedNavigationTimeout,
)

FAULT_PLAN_VARIABLE = "DAILYSCRAPER_TEST_FAULT_PLAN"


class FaultInjector(Protocol):
    def hit(self, point: FaultPoint | str, **context: FaultScalar) -> None: ...


class NullFaultInjector:
    def hit(self, point: FaultPoint | str, **context: FaultScalar) -> None:
        return None


NULL_FAULT_INJECTOR = NullFaultInjector()


class PlannedFaultInjector:
    """Activations are claimed as files, so a rule's ``times`` limit holds across processes."""

    def __init__(
        self,
        plan: FaultPlan,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        exit_fn: Callable[[int], None] = os._exit,
    ) -> None:
        self.plan = plan
        self.sleep_fn = sleep_fn
        self.exit_fn = exit_fn
        self._matching_occurrences: dict[str, int] = {}
        self._state_root = plan.source.parent / ".fault-events" / plan.source.stem

    def hit(self, point: FaultPoint | str, **context: FaultScalar) -> None:
        normalized_point = FaultPoint(point)
        for rule in self.plan.rules:
            if rule.point is not normalized_point:
                continue
            if not _matches(rule.match, context):
                continue
            seen = self._matching_occurrences.get(rule.rule_id, 0) + 1
            self._matching_occurrences[rule.rule_id] = seen
            if seen < rule.occurrence:
                continue
            activation = self._claim_activation(rule)
            if activation is None:
                continue
            self._record_activation(rule, activation, context)
            self._activate(rule, context)

    def _claim_activation(self, rule: FaultRule) -> int | None:
        claims = self._state_root / "claims"
        claims.mkdir(parents=True, exist_ok=True)
        for activation in range(1, rule.times + 1):
            claim = claims / f"{rule.rule_id}--{activation}.claim"
            try:
                with claim.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps({"pid": os.getpid(), "activation": activation}))
                return activation
            except FileExistsError:
                continue
        return None

    def _record_activation(
        self,
        rule: FaultRule,
        activation: int,
        context: Mapping[str, FaultScalar],
    ) -> None:
        events = self._state_root / "events"
        events.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "rule_id": rule.rule_id,
            "point": rule.point.value,
            "fault": rule.fault_type.value,
            "activation": activation,
            "context": dict(context),
            "parameters": dict(rule.parameters or {}),
        }
        with (events / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _activate(self, rule: FaultRule, context: Mapping[str, FaultScalar]) -> None:
        parameters = rule.parameters or {}
        match rule.fault_type:
            case FaultType.NAVIGATION_TIMEOUT:
                raise InjectedNavigationTimeout(
                    rule.rule_id,
                    rule.fault_type,
                    f"Injected navigation timeout by fault rule {rule.rule_id}",
                )
            case FaultType.BROWSER_CLOSE:
                raise InjectedBrowserClose(
                    rule.rule_id,
                    rule.fault_type,
                    f"Injected browser close by fault rule {rule.rule_id}: browser has been closed",
                )
            case FaultType.HTTP_STATUS:
                status_code = int(parameters["status_code"])
                raise InjectedHttpStatus(
                    rule.rule_id,
                    rule.fault_type,
                    f"Injected HTTP status {status_code} by fault rule {rule.rule_id}",
                    status_code=status_code,
                )
            case FaultType.SLOT_CRASH:
                self.exit_fn(int(parameters.get("exit_code", 86)))
                raise RuntimeError("Slot-crash exit function unexpectedly returned.")
            case FaultType.FILESYSTEM_PERMISSION_ERROR:
                raise PermissionError(
                    errno.EACCES,
                    f"Injected filesystem_permission_error by fault rule {rule.rule_id}",
                    str(context.get("path_name") or "artifact"),
                )
            case FaultType.FILESYSTEM_OS_ERROR:
                raise OSError(
                    int(parameters.get("errno", errno.EBUSY)),
                    f"Injected filesystem_os_error by fault rule {rule.rule_id}",
                    str(context.get("path_name") or "artifact"),
                )
            case FaultType.SQLITE_BUSY:
                raise sqlite3.OperationalError(
                    f"database is busy: injected by fault rule {rule.rule_id}"
                )
            case FaultType.SQLITE_LOCKED:
                raise sqlite3.OperationalError(
                    f"database is locked: injected by fault rule {rule.rule_id}"
                )
            case FaultType.DELAY:
                self.sleep_fn(float(parameters.get("seconds", 0)))
            case _:
                raise AssertionError(f"Unhandled fault type: {rule.fault_type}")


def get_fault_injector() -> FaultInjector:
    plan_value = str(os.getenv(FAULT_PLAN_VARIABLE) or "").strip()
    if not plan_value:
        return NULL_FAULT_INJECTOR

    if current_environment() != TESTING_ENVIRONMENT:
        raise FaultPlanError(
            f"{FAULT_PLAN_VARIABLE} is allowed only in the {TESTING_ENVIRONMENT} environment."
        )
    workspace_value = str(os.getenv(TEST_WORKSPACE_VARIABLE) or "").strip()
    if not workspace_value:
        raise FaultPlanError(f"{TEST_WORKSPACE_VARIABLE} is required for fault injection.")
    workspace = Path(workspace_value).resolve()
    source = Path(plan_value).resolve()
    if not source.is_relative_to(workspace):
        raise FaultPlanError(f"Fault plan must be inside the isolated testing workspace: {source}")
    stat = source.stat() if source.is_file() else None
    signature = (stat.st_mtime_ns, stat.st_size) if stat is not None else (0, 0)
    return _cached_fault_injector(str(source), signature)


@lru_cache(maxsize=16)
def _cached_fault_injector(source: str, signature: tuple[int, int]) -> PlannedFaultInjector:
    del signature  # part of the cache key only: a changed plan file is reloaded
    return PlannedFaultInjector(FaultPlan.load(Path(source)))


def reset_fault_injector_cache() -> None:
    _cached_fault_injector.cache_clear()


def read_fault_events(plan_path: Path) -> list[dict[str, Any]]:
    source = Path(plan_path).resolve()
    events_root = source.parent / ".fault-events" / source.stem / "events"
    if not events_root.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for event_file in sorted(events_root.glob("*.jsonl")):
        for line in event_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _matches(expected: Mapping[str, FaultScalar], actual: Mapping[str, FaultScalar]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())

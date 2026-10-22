"""Typed JSON schema for deterministic fault plans."""

from __future__ import annotations

import errno
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

FAULT_PLAN_VERSION = 1
MAX_FAULT_PLAN_BYTES = 256 * 1024
_RULE_ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")


class FaultPoint(StrEnum):
    NAVIGATION_BEFORE_OPEN = "navigation.before_open"
    NAVIGATION_AFTER_OPEN = "navigation.after_open"
    # Match context carries ``task_kind`` (probe/discover/list/detail).
    SLOT_AFTER_SESSION_OPEN = "slot.after_session_open"
    SLOT_AFTER_TASK_STARTED = "slot.after_task_started"
    FILESYSTEM_REQUIRED_BEFORE_REPLACE = "filesystem.required.before_replace"
    FILESYSTEM_BEST_EFFORT_BEFORE_REPLACE = "filesystem.best_effort.before_replace"
    FILESYSTEM_OUTPUT_BEFORE_REPLACE = "filesystem.output.before_replace"
    SQLITE_BEFORE_OPERATION = "sqlite.before_operation"
    NOTIFY_BEFORE_SEND = "notify.before_send"


class FaultType(StrEnum):
    NAVIGATION_TIMEOUT = "navigation_timeout"
    HTTP_STATUS = "http_status"
    BROWSER_CLOSE = "browser_close"
    SLOT_CRASH = "slot_crash"
    FILESYSTEM_PERMISSION_ERROR = "filesystem_permission_error"
    FILESYSTEM_OS_ERROR = "filesystem_os_error"
    SQLITE_BUSY = "sqlite_busy"
    SQLITE_LOCKED = "sqlite_locked"
    DELAY = "delay"


FILESYSTEM_POINTS = frozenset(
    {
        FaultPoint.FILESYSTEM_REQUIRED_BEFORE_REPLACE,
        FaultPoint.FILESYSTEM_BEST_EFFORT_BEFORE_REPLACE,
        FaultPoint.FILESYSTEM_OUTPUT_BEFORE_REPLACE,
    }
)

# DELAY is allowed at every point; every other fault only where it is meaningful.
ALLOWED_POINTS: dict[FaultType, frozenset[FaultPoint]] = {
    FaultType.NAVIGATION_TIMEOUT: frozenset({FaultPoint.NAVIGATION_BEFORE_OPEN}),
    FaultType.HTTP_STATUS: frozenset({FaultPoint.NAVIGATION_AFTER_OPEN}),
    FaultType.BROWSER_CLOSE: frozenset({FaultPoint.SLOT_AFTER_SESSION_OPEN}),
    FaultType.SLOT_CRASH: frozenset({FaultPoint.SLOT_AFTER_TASK_STARTED}),
    FaultType.FILESYSTEM_PERMISSION_ERROR: FILESYSTEM_POINTS,
    FaultType.FILESYSTEM_OS_ERROR: FILESYSTEM_POINTS,
    FaultType.SQLITE_BUSY: frozenset({FaultPoint.SQLITE_BEFORE_OPERATION}),
    FaultType.SQLITE_LOCKED: frozenset({FaultPoint.SQLITE_BEFORE_OPERATION}),
}

TRANSIENT_FILESYSTEM_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        errno.EBUSY,
        errno.EINTR,
        getattr(errno, "ETXTBSY", errno.EBUSY),
    }
)


class FaultPlanError(RuntimeError):
    """Raised when a test fault plan is unsafe or malformed."""


class InjectedFault(RuntimeError):
    def __init__(self, rule_id: str, fault_type: FaultType, message: str):
        super().__init__(message)
        self.rule_id = rule_id
        self.fault_type = fault_type


class InjectedNavigationTimeout(InjectedFault):
    pass


class InjectedBrowserClose(InjectedFault):
    pass


class InjectedHttpStatus(InjectedFault):
    def __init__(
        self,
        rule_id: str,
        fault_type: FaultType,
        message: str,
        *,
        status_code: int,
    ) -> None:
        super().__init__(rule_id, fault_type, message)
        self.status_code = status_code


type FaultScalar = str | int | float | bool | None


@dataclass(frozen=True)
class FaultRule:
    rule_id: str
    point: FaultPoint
    fault_type: FaultType
    match: Mapping[str, FaultScalar]
    occurrence: int = 1
    times: int = 1
    parameters: Mapping[str, FaultScalar] | None = None

    @classmethod
    def from_payload(cls, payload: Any, *, index: int) -> FaultRule:
        if not isinstance(payload, dict):
            raise FaultPlanError(f"Fault rule #{index} must be an object.")
        unknown = set(payload) - {
            "id",
            "point",
            "fault",
            "match",
            "occurrence",
            "times",
            "parameters",
        }
        if unknown:
            raise FaultPlanError(f"Fault rule #{index} has unknown fields: {sorted(unknown)}")

        rule_id = str(payload.get("id") or "").strip()
        if not _RULE_ID.fullmatch(rule_id):
            raise FaultPlanError(f"Fault rule #{index} has invalid id: {rule_id!r}")
        try:
            point = FaultPoint(str(payload.get("point") or ""))
        except ValueError as error:
            raise FaultPlanError(f"Fault rule {rule_id!r} has unsupported point.") from error
        try:
            fault_type = FaultType(str(payload.get("fault") or ""))
        except ValueError as error:
            raise FaultPlanError(f"Fault rule {rule_id!r} has unsupported fault.") from error

        match = _scalar_mapping(payload.get("match", {}), field=f"fault rule {rule_id!r} match")
        parameters = _scalar_mapping(
            payload.get("parameters", {}),
            field=f"fault rule {rule_id!r} parameters",
        )
        occurrence = _positive_int(
            payload.get("occurrence", 1),
            field=f"fault rule {rule_id!r} occurrence",
        )
        times = _positive_int(payload.get("times", 1), field=f"fault rule {rule_id!r} times")
        _validate_fault_point(rule_id, point, fault_type)
        _validate_parameters(rule_id, fault_type, parameters)
        return cls(
            rule_id=rule_id,
            point=point,
            fault_type=fault_type,
            match=match,
            occurrence=occurrence,
            times=times,
            parameters=parameters,
        )


@dataclass(frozen=True)
class FaultPlan:
    source: Path
    rules: tuple[FaultRule, ...]

    @classmethod
    def load(cls, path: Path) -> FaultPlan:
        source = Path(path).resolve()
        if not source.is_file():
            raise FaultPlanError(f"Fault plan does not exist: {source}")
        if source.stat().st_size > MAX_FAULT_PLAN_BYTES:
            raise FaultPlanError(f"Fault plan exceeds {MAX_FAULT_PLAN_BYTES} bytes: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FaultPlanError(f"Cannot read fault plan: {source}") from error
        if not isinstance(payload, dict):
            raise FaultPlanError("Fault plan root must be an object.")
        unknown = set(payload) - {"version", "faults"}
        if unknown:
            raise FaultPlanError(f"Fault plan has unknown fields: {sorted(unknown)}")
        if payload.get("version") != FAULT_PLAN_VERSION:
            raise FaultPlanError(f"Fault plan version must be {FAULT_PLAN_VERSION}.")
        faults = payload.get("faults")
        if not isinstance(faults, list):
            raise FaultPlanError("Fault plan 'faults' must be a list.")
        rules = tuple(
            FaultRule.from_payload(rule, index=index) for index, rule in enumerate(faults, start=1)
        )
        rule_ids = [rule.rule_id for rule in rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise FaultPlanError("Fault rule ids must be unique.")
        return cls(source=source, rules=rules)


def _scalar_mapping(payload: Any, *, field: str) -> dict[str, FaultScalar]:
    if not isinstance(payload, dict):
        raise FaultPlanError(f"{field} must be an object.")
    result: dict[str, FaultScalar] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise FaultPlanError(f"{field} keys must be non-empty strings.")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise FaultPlanError(f"{field}.{key} must be a scalar value.")
        result[key] = value
    return result


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FaultPlanError(f"{field} must be a positive integer.")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_parameters(
    rule_id: str,
    fault_type: FaultType,
    parameters: Mapping[str, FaultScalar],
) -> None:
    allowed = {
        FaultType.DELAY: {"seconds"},
        FaultType.HTTP_STATUS: {"status_code"},
        FaultType.SLOT_CRASH: {"exit_code"},
        FaultType.FILESYSTEM_OS_ERROR: {"errno"},
    }.get(fault_type, set())
    unknown = set(parameters) - allowed
    if unknown:
        raise FaultPlanError(
            f"Fault rule {rule_id!r} has unsupported parameters: {sorted(unknown)}"
        )

    if fault_type is FaultType.DELAY:
        seconds = parameters.get("seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not 0 < float(seconds) <= 3600
        ):
            raise FaultPlanError(f"Fault rule {rule_id!r} delay seconds must be in (0, 3600].")
    elif fault_type is FaultType.HTTP_STATUS:
        status_code = parameters.get("status_code")
        if not _is_int(status_code) or not 400 <= status_code <= 599:
            raise FaultPlanError(
                f"Fault rule {rule_id!r} HTTP status_code must be an integer in [400, 599]."
            )
    elif fault_type is FaultType.SLOT_CRASH:
        exit_code = parameters.get("exit_code", 86)
        if not _is_int(exit_code) or not 1 <= exit_code <= 255:
            raise FaultPlanError(
                f"Fault rule {rule_id!r} slot exit_code must be an integer in [1, 255]."
            )
    elif fault_type is FaultType.FILESYSTEM_OS_ERROR:
        error_code = parameters.get("errno", errno.EBUSY)
        if not _is_int(error_code) or error_code not in TRANSIENT_FILESYSTEM_ERRNOS:
            raise FaultPlanError(
                f"Fault rule {rule_id!r} filesystem errno must be one of "
                f"{sorted(TRANSIENT_FILESYSTEM_ERRNOS)}."
            )


def _validate_fault_point(rule_id: str, point: FaultPoint, fault_type: FaultType) -> None:
    if fault_type is FaultType.DELAY:
        return
    if point not in ALLOWED_POINTS[fault_type]:
        raise FaultPlanError(
            f"Fault rule {rule_id!r} cannot use fault {fault_type.value!r} "
            f"at point {point.value!r}."
        )

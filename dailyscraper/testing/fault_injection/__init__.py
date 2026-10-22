"""Deterministic fault injection, active only with an explicit testing plan."""

from .models import (
    FAULT_PLAN_VERSION,
    FaultPlan,
    FaultPlanError,
    FaultPoint,
    FaultRule,
    FaultScalar,
    FaultType,
    InjectedBrowserClose,
    InjectedFault,
    InjectedHttpStatus,
    InjectedNavigationTimeout,
)
from .runtime import (
    FAULT_PLAN_VARIABLE,
    NULL_FAULT_INJECTOR,
    FaultInjector,
    NullFaultInjector,
    PlannedFaultInjector,
    get_fault_injector,
    read_fault_events,
    reset_fault_injector_cache,
)

__all__ = [
    "FAULT_PLAN_VARIABLE",
    "FAULT_PLAN_VERSION",
    "NULL_FAULT_INJECTOR",
    "FaultInjector",
    "FaultPlan",
    "FaultPlanError",
    "FaultPoint",
    "FaultRule",
    "FaultScalar",
    "FaultType",
    "InjectedBrowserClose",
    "InjectedFault",
    "InjectedHttpStatus",
    "InjectedNavigationTimeout",
    "NullFaultInjector",
    "PlannedFaultInjector",
    "get_fault_injector",
    "read_fault_events",
    "reset_fault_injector_cache",
]

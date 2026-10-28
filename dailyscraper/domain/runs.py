from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

DEFAULT_PROJECT_ID = "default"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self is not RunStatus.RUNNING

    @property
    def is_completed(self) -> bool:
        """The run went through all its work, possibly with failed items."""
        return self in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ERRORS}


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    project_id: str
    status: RunStatus
    started_at: str
    scrapers: tuple[str, ...]
    packages: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    config: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    finished_at: str | None = None
    summary: Mapping[str, Any] | None = None
    error: str | None = None
    host_id: str | None = None
    pid: int | None = None


def new_run_id(now: datetime) -> str:
    """Sortable by start time and unique without a central counter."""
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"

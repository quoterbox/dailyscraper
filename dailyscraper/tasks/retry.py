from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dailyscraper.domain import AttemptStatus, ErrorCategory, Task, TaskKind, TaskOutcome

RETRYABLE = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.NETWORK,
        ErrorCategory.LOAD_FAILED,
        ErrorCategory.BLANK,
        ErrorCategory.BROWSER_CLOSED,
        ErrorCategory.BLOCKED,
        ErrorCategory.RATE_LIMITED,
        ErrorCategory.CAPTCHA,
        ErrorCategory.PROXY_AUTH,
        ErrorCategory.INTERNAL,
    }
)
RETRYABLE_STATUSES = frozenset(category.value for category in RETRYABLE)


class Verdict(StrEnum):
    FINISH = "finish"
    RETRY = "retry"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    delay_seconds: float = 0.0
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RetryPolicy:
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 120.0

    def delay(self, attempt_no: int) -> float:
        return min(self.max_delay_seconds, self.base_delay_seconds * 2 ** max(0, attempt_no - 1))

    def decide(
        self, task: Task, outcome: TaskOutcome, done_rows: Mapping[int, str] | None = None
    ) -> Decision:
        """Retry what is worth another attempt while attempts are left.

        ``done_rows`` are the attempt's registered detail rows; for a detail task
        only rows without a final status are tried again.
        """
        if outcome.status is AttemptStatus.CANCELLED or task.attempts_left == 0:
            return Decision(Verdict.FINISH)
        if task.kind is TaskKind.DETAIL:
            rows = _rows_to_retry(task, outcome, done_rows or {})
            if rows:
                return Decision(Verdict.RETRY, self.delay(task.attempts), {"rows": rows})
            return Decision(Verdict.FINISH)
        if outcome.status is AttemptStatus.FAILED and outcome.category in RETRYABLE:
            return Decision(Verdict.RETRY, self.delay(task.attempts))
        return Decision(Verdict.FINISH)


def _rows_to_retry(task: Task, outcome: TaskOutcome, done_rows: Mapping[int, str]) -> list[int]:
    if outcome.status is AttemptStatus.COMPLETED:
        return sorted(set(outcome.retry_rows))
    if outcome.category is not None and outcome.category not in RETRYABLE:
        return []
    planned: Sequence[int] = task.payload.get("rows", ())
    return [row for row in planned if row not in done_rows or done_rows[row] in RETRYABLE_STATUSES]

"""Whether a scraper's health probe collected what a real run needs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dailyscraper.domain import ErrorCategory
from dailyscraper.schema.scraper import HealthCheck

MAX_SAMPLE_LENGTH = 200


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    reason: str
    category: ErrorCategory | None = None
    items: int = 0
    complete: int = 0
    missing: tuple[str, ...] = ()
    sample: Mapping[str, str] | None = None

    def details(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "items": self.items,
            "complete": self.complete,
            "missing": list(self.missing),
            "sample": dict(self.sample or {}),
        }


def evaluate(
    items: Sequence[Mapping[str, Any]],
    check: HealthCheck,
    *,
    required_fields: Sequence[str] = (),
    page_category: ErrorCategory | None = None,
    page_ready: bool = True,
    stage: str = "list",
) -> HealthReport:
    """``page_category`` is the page's failure, if any: blocks are the proxy's problem.

    Too few items on a page that opened fine, or items without the required
    fields, mean broken selectors; another proxy would not help.
    """
    sample = _sample(items[0]) if items else None
    if page_category is not None and page_category is not ErrorCategory.NOT_FOUND:
        return HealthReport(
            False, f"{stage}_page_{page_category.value}", page_category, len(items), sample=sample
        )
    if len(items) < check.min_items:
        reason = f"{stage}_selectors_broken" if page_ready else f"{stage}_no_items"
        return HealthReport(False, reason, ErrorCategory.INVALID_CONTENT, len(items), sample=sample)
    complete = sum(1 for item in items if all(_filled(item, name) for name in required_fields))
    if required_fields and complete < check.min_items:
        missing = tuple(
            name for name in required_fields if not any(_filled(item, name) for item in items)
        )
        return HealthReport(
            False,
            f"{stage}_required_fields_incomplete",
            ErrorCategory.INVALID_CONTENT,
            len(items),
            complete,
            missing,
            sample,
        )
    return HealthReport(True, "scraper_health_ok", None, len(items), complete, (), sample)


def _filled(item: Mapping[str, Any], name: str) -> bool:
    value = item.get(name)
    return value is not None and str(value).strip() != ""


def _sample(item: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(value)[:MAX_SAMPLE_LENGTH] for key, value in item.items()}

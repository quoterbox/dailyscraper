"""List tasks from a discovery result, under the package's policies for blocks and doubt."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from dailyscraper.domain import AttemptStatus, Task, TaskKind, TaskOutcome, TaskSpec
from dailyscraper.schema import ScraperPackage
from dailyscraper.tasks.planner import list_range_tasks

ALL_PAGES = -1


@dataclass(frozen=True)
class FollowUp:
    specs: list[TaskSpec] = field(default_factory=list)
    last_page: int | None = None
    error: str | None = None


def after_discovery(
    task: Task,
    outcome: TaskOutcome,
    package: ScraperPackage,
    *,
    pages_per_task: int,
    max_attempts: int,
    saved_pages: Collection[int] = (),
) -> FollowUp:
    """List tasks for the pages discovery neither ruled out nor already saved.

    ``saved_pages`` belong to the probed link only: the links sharing its
    pages list them in another order. ``ALL_PAGES`` as the last page: the
    links are read whole, without a range.
    """
    if outcome.status is AttemptStatus.CANCELLED:
        return FollowUp()
    definition = package.definition
    policy = definition.discovery
    pagination = definition.pages.list.pagination
    start = pagination.start_page
    configured = pagination.last_page
    first_chunk = start + max(pages_per_task, 1) - 1
    if configured:
        first_chunk = min(first_chunk, configured)
    details = outcome.details or {}
    status = details.get("status") or "unknown"
    last = details.get("last_page")
    reason = details.get("reason") or outcome.error or status

    if status == "ok":
        effective = last
    elif status == "empty":
        effective = None
    elif status == "blocked":
        if policy.on_blocked == "fail_run":
            return FollowUp(error=f"discovery blocked: {reason}")
        effective = last or (first_chunk if policy.on_blocked == "fallback_first_chunk" else None)
    elif status in {"proxy_auth", "failed"}:
        effective = last
    else:
        if policy.on_unknown == "fail_run":
            return FollowUp(error=f"discovery unknown: {reason}")
        if policy.on_unknown == "fallback_config_limit":
            effective = configured or ALL_PAGES
        elif policy.on_unknown == "fallback_first_chunk":
            effective = last or first_chunk
        else:
            effective = last
    if effective is None:
        return FollowUp(last_page=None)

    links = details.get("links") or task.payload.get("links") or [task.payload["link"]]
    specs: list[TaskSpec] = []
    for number, link in enumerate(links, start=1):
        prefix = f"{task.task_id}:list:{number:03d}"
        skip = set(saved_pages) if link == task.payload["link"] else set()
        if effective == ALL_PAGES and skip:
            # The end is unknown: the gaps below the last saved page, then onwards.
            known = max(skip)
            specs += list_range_tasks(
                prefix,
                task.run_id,
                task.scraper,
                link,
                start,
                known,
                pages_per_task=pages_per_task,
                max_attempts=max_attempts,
                skip=skip,
            )
            specs.append(
                TaskSpec(
                    task_id=f"{prefix}:rest",
                    run_id=task.run_id,
                    scraper=task.scraper,
                    kind=TaskKind.LIST,
                    payload={"link": link, "pages": [known + 1, 0]},
                    max_attempts=max_attempts,
                )
            )
            continue
        if effective == ALL_PAGES:
            specs.append(
                TaskSpec(
                    task_id=prefix,
                    run_id=task.run_id,
                    scraper=task.scraper,
                    kind=TaskKind.LIST,
                    payload={"link": link},
                    max_attempts=max_attempts,
                )
            )
            continue
        specs += list_range_tasks(
            prefix,
            task.run_id,
            task.scraper,
            link,
            start,
            effective,
            pages_per_task=pages_per_task,
            max_attempts=max_attempts,
            skip=skip,
        )
    return FollowUp(specs, effective)

from __future__ import annotations

from dailyscraper.domain import TaskKind, TaskSpec
from dailyscraper.schema import ScraperPackage
from dailyscraper.tasks.inputs import InputSnapshot


class PlanningError(ValueError):
    pass


def plan_tasks(
    run_id: str,
    package: ScraperPackage,
    *,
    snapshot: InputSnapshot | None = None,
    items_per_task: int = 50,
    max_attempts: int = 3,
) -> list[TaskSpec]:
    """List tasks per start link, or detail tasks over chunks of the input snapshot."""
    name = package.name
    pages = package.definition.pages
    if snapshot is not None:
        if pages.detail is None:
            raise PlanningError(f"{name} has no detail page for input rows")
        first = pages.detail.start_item
        last = min(snapshot.rows, pages.detail.last_item or snapshot.rows)
        positions = list(range(first, last + 1))
        chunks = [
            positions[index : index + items_per_task]
            for index in range(0, len(positions), items_per_task)
        ]
        return [
            TaskSpec(
                task_id=f"{run_id}:{name}:detail:{number:05d}",
                run_id=run_id,
                scraper=name,
                kind=TaskKind.DETAIL,
                payload={"rows": chunk},
                max_attempts=max_attempts,
            )
            for number, chunk in enumerate(chunks, start=1)
        ]

    if pages.list is None:
        raise PlanningError(f"{name} has no list page; pass an input file with detail rows")
    groups = package.definition.links
    if any(group.type == "template" for group in groups):
        raise PlanningError("template link groups need a workload with page ranges")
    links = [url for group in groups for url in group.urls]
    if not links:
        raise PlanningError(f"{name} has no links; pass an input file with detail rows")
    return [
        TaskSpec(
            task_id=f"{run_id}:{name}:list:{number:05d}",
            run_id=run_id,
            scraper=name,
            kind=TaskKind.LIST,
            payload={"link": link},
            max_attempts=max_attempts,
        )
        for number, link in enumerate(links, start=1)
    ]

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dailyscraper.domain import TaskKind, TaskSpec
from dailyscraper.schema import ScraperPackage
from dailyscraper.tasks.inputs import InputSnapshot

PROBE_PRIORITY = 100
DISCOVER_PRIORITY = 50
# Parameters that change the order of a catalog, not the number of its pages.
PAGE_INDEPENDENT_PARAMS = frozenset({"page", "sorting", "sort", "order", "order_by"})


class PlanningError(ValueError):
    pass


def plan_tasks(
    run_id: str,
    package: ScraperPackage,
    *,
    snapshot: InputSnapshot | None = None,
    items_per_task: int = 50,
    pages_per_task: int = 0,
    max_attempts: int = 3,
) -> list[TaskSpec]:
    """The run's first tasks for one scraper: its health probe, then discovery or work.

    Links become ``discover`` tasks when discovery is on, list tasks over page
    ranges when the last page is configured, otherwise one list task each;
    input rows become detail tasks over chunks of the snapshot (without a
    detail page the package collects each row itself).
    """
    probe = _probe(run_id, package, snapshot, max_attempts)
    if snapshot is not None:
        return probe + _detail_tasks(run_id, package, snapshot, items_per_task, max_attempts)
    return probe + _list_tasks(run_id, package, pages_per_task, max_attempts)


def list_range_tasks(
    task_prefix: str,
    run_id: str,
    scraper: str,
    link: str,
    first: int,
    last: int,
    *,
    pages_per_task: int,
    max_attempts: int,
    skip: Collection[int] = (),
) -> list[TaskSpec]:
    """List tasks over ``first…last`` without the ``skip`` pages, up to ``pages_per_task`` each."""
    return [
        TaskSpec(
            task_id=f"{task_prefix}:{number:03d}",
            run_id=run_id,
            scraper=scraper,
            kind=TaskKind.LIST,
            payload={"link": link, "pages": [start, end]},
            max_attempts=max_attempts,
        )
        for number, (start, end) in enumerate(
            page_ranges(first, last, pages_per_task, skip), start=1
        )
    ]


def page_ranges(
    first: int, last: int, size: int, skip: Collection[int] = ()
) -> list[tuple[int, int]]:
    """Consecutive runs of pages not in ``skip``, cut to ``size`` pages (0: no cut)."""
    ranges: list[tuple[int, int]] = []
    page = first
    while page <= last:
        if page in skip:
            page += 1
            continue
        end = page
        while end < last and end + 1 not in skip and (not size or end + 1 - page < size):
            end += 1
        ranges.append((page, end))
        page = end + 1
    return ranges


def page_count_key(link: str) -> str:
    """Links with the same key have the same pages: they differ only in sorting."""
    parts = urlsplit(link)
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in PAGE_INDEPENDENT_PARAMS
    )
    return urlunsplit(parts._replace(query=urlencode(query)))


def _probe(
    run_id: str, package: ScraperPackage, snapshot: InputSnapshot | None, max_attempts: int
) -> list[TaskSpec]:
    definition = package.definition
    check = definition.health_check
    if check is None or not check.enabled:
        return []
    if snapshot is not None:
        detail = definition.pages.detail
        first = detail.start_item if detail is not None else 1
        last = min(snapshot.rows, first + check.max_sample_urls - 1)
        if last < first:
            return []
        payload: dict = {"rows": list(range(first, last + 1))}
    else:
        links = check.urls or tuple(url for group in definition.links for url in group.urls)[:1]
        if not links:
            return []
        payload = {"links": list(links)}
    return [
        TaskSpec(
            task_id=f"{run_id}:{package.name}:probe",
            run_id=run_id,
            scraper=package.name,
            kind=TaskKind.PROBE,
            payload=payload,
            priority=PROBE_PRIORITY,
            max_attempts=max_attempts,
        )
    ]


def _detail_tasks(
    run_id: str,
    package: ScraperPackage,
    snapshot: InputSnapshot,
    items_per_task: int,
    max_attempts: int,
) -> list[TaskSpec]:
    name = package.name
    detail = package.definition.pages.detail
    first = detail.start_item if detail is not None else 1
    last = min(snapshot.rows, (detail.last_item if detail is not None else 0) or snapshot.rows)
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


def _list_tasks(
    run_id: str, package: ScraperPackage, pages_per_task: int, max_attempts: int
) -> list[TaskSpec]:
    name = package.name
    definition = package.definition
    page = definition.pages.list
    if page is None:
        raise PlanningError(f"{name} has no list page; pass an input file with detail rows")
    groups = definition.links
    if any(group.type == "template" for group in groups):
        raise PlanningError("template link groups need a workload with page ranges")
    links = [url for group in groups for url in group.urls]
    if not links:
        raise PlanningError(f"{name} has no links; pass an input file with detail rows")
    pagination = page.pagination
    if definition.discovery.enabled:
        # Without an address template only the package's discover_last_page can search.
        return _discover_tasks(run_id, name, links, max_attempts)
    if pagination.url_template is not None and pagination.last_page and pages_per_task:
        return [
            spec
            for number, link in enumerate(links, start=1)
            for spec in list_range_tasks(
                f"{run_id}:{name}:list:{number:05d}",
                run_id,
                name,
                link,
                pagination.start_page,
                pagination.last_page,
                pages_per_task=pages_per_task,
                max_attempts=max_attempts,
            )
        ]
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


def _discover_tasks(run_id: str, name: str, links: list[str], max_attempts: int) -> list[TaskSpec]:
    shared: dict[str, list[str]] = {}
    for link in links:
        shared.setdefault(page_count_key(link), []).append(link)
    return [
        TaskSpec(
            task_id=f"{run_id}:{name}:discover:{number:05d}",
            run_id=run_id,
            scraper=name,
            kind=TaskKind.DISCOVER,
            payload={"link": same[0], "links": same},
            priority=DISCOVER_PRIORITY,
            max_attempts=max_attempts,
        )
        for number, same in enumerate(shared.values(), start=1)
    ]

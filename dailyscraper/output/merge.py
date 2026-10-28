from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dailyscraper.domain import CommitRecord
from dailyscraper.storage import PartitionRegistry, RunLayout, iter_committed_records
from dailyscraper.tasks.inputs import InputSnapshot

Row = dict[str, Any]


def scraper_records(
    registry: PartitionRegistry,
    layout: RunLayout,
    run_id: str,
    scraper: str,
    snapshot: InputSnapshot | None = None,
) -> Iterator[dict[str, Any]]:
    """Committed partition records that make up the result.

    With an input every row is decided by its latest commit, so all commits
    count. Without an input a task's rows come from its latest attempt that
    committed any, so a retried list page does not appear twice.
    """
    commits = registry.commits(run_id, scraper)
    if snapshot is None:
        commits = latest_attempt_commits(commits)
    return iter_committed_records(registry, layout, run_id, scraper, commits=commits)


def scraper_rows(
    registry: PartitionRegistry,
    layout: RunLayout,
    run_id: str,
    scraper: str,
    snapshot: InputSnapshot | None = None,
) -> Iterator[Row]:
    """Final rows of one scraper in a run.

    Without an input the rows follow commit order. With an input every input
    row appears once, in input order, replaced by its latest committed detail
    result; rows without a result keep the input values.
    """
    records = scraper_records(registry, layout, run_id, scraper, snapshot)
    if snapshot is None:
        for record in records:
            yield record["data"]
        return
    latest: dict[int, Row] = {}
    for record in records:
        if record.get("input_row") is not None:
            latest[record["input_row"]] = record["data"]
    for position, row in enumerate(snapshot.iter_rows(), start=1):
        yield latest.get(position, row)


def latest_attempt_commits(commits: list[CommitRecord]) -> list[CommitRecord]:
    chosen: dict[str, int] = {}
    for commit in commits:
        if commit.task_id is not None and commit.attempt_id is not None:
            number = _attempt_number(commit.attempt_id)
            chosen[commit.task_id] = max(chosen.get(commit.task_id, 0), number)
    return [
        commit
        for commit in commits
        if commit.task_id is None
        or commit.attempt_id is None
        or _attempt_number(commit.attempt_id) == chosen[commit.task_id]
    ]


def _attempt_number(attempt_id: str) -> int:
    _, _, number = attempt_id.rpartition("#")
    return int(number) if number.isdigit() else 0

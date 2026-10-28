from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dailyscraper.storage import PartitionRegistry, RunLayout, iter_committed_records
from dailyscraper.tasks.inputs import InputSnapshot

Row = dict[str, Any]


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
    records = iter_committed_records(registry, layout, run_id, scraper)
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

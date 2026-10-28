from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dailyscraper.app.session import open_pipeline
from dailyscraper.domain import (
    DeliveryRecord,
    EventLevel,
    PageOutcome,
    RunRecord,
    RunStatus,
    new_run_id,
)
from dailyscraper.execution.partitions import ItemIdentity, PartitionSink, PartitionWriter
from dailyscraper.execution.sink import ItemSink
from dailyscraper.observability import RunEvents, redact
from dailyscraper.output import Deliverer, DeliveryContext, make_archive, scraper_rows, write_output
from dailyscraper.pipelines import DefaultPipeline, PipelineResult
from dailyscraper.schema import Inputs, ScraperPackage, load_scraper_package
from dailyscraper.settings import Settings, load_variables
from dailyscraper.settings.environment import resolve_environment_paths
from dailyscraper.storage import RunLayout, Store
from dailyscraper.tasks.inputs import InputSnapshot, create_input_snapshot

logger = logging.getLogger(__name__)

LOCAL_WRITER_ID = "local"
MAX_FAILURE_EVENTS = 1000

PipelineOpener = Callable[
    [ScraperPackage, Settings, ItemSink], AbstractContextManager[DefaultPipeline]
]


@dataclass(frozen=True)
class RunRequest:
    scraper: str
    input_path: Path | None = None


@dataclass(frozen=True)
class RunReport:
    run: RunRecord
    layout: RunLayout
    outputs: dict[str, list[Path]] = field(default_factory=dict)
    deliveries: list[DeliveryRecord] = field(default_factory=list)


class RunService:
    """Runs one scraper in this process and finalizes the run whatever happens."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        pipeline_opener: PipelineOpener = open_pipeline,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.store = store
        self._open_pipeline = pipeline_opener
        self._now = now
        self._sleep = sleep

    def load_package(self, name: str) -> ScraperPackage:
        variables = load_variables(resolve_environment_paths(self.settings.project_root))
        return load_scraper_package(self.settings.paths.scrapers / name, variables=variables)

    def run(self, request: RunRequest) -> RunReport:
        package = self.load_package(request.scraper)
        started = self._now()
        run_id = new_run_id(started)
        layout = RunLayout.for_run(self.settings.paths.data, run_id)
        layout.root.mkdir(parents=True)
        config = redact(self.settings.model_dump(mode="json"))
        layout.config.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
        self.store.runs.create(
            RunRecord(
                run_id=run_id,
                project_id=self.settings.project_id,
                status=RunStatus.RUNNING,
                started_at=self.store.now(),
                scrapers=(package.name,),
                packages={package.name: package.version},
                config=config,
            )
        )
        events = RunEvents(self.store, run_id=run_id, project_id=self.settings.project_id)
        events.emit("run.started", scraper=package.name, version=package.version)
        logger.info("Run %s started: %s (%s)", run_id, package.name, package.version)

        sink = self._sink(package, layout, run_id)
        execution = _Execution()
        try:
            self._execute(package, request, layout, sink, execution)
        except KeyboardInterrupt:
            execution.fail(RunStatus.INTERRUPTED, "interrupted")
            logger.warning("Run %s interrupted", run_id)
        except Exception as error:
            execution.fail(RunStatus.FAILED, f"{type(error).__name__}: {error}")
            logger.exception("Run %s failed", run_id)
        return self._finalize(package, layout, run_id, started, sink, execution, events)

    def _sink(self, package: ScraperPackage, layout: RunLayout, run_id: str) -> PartitionSink:
        storage = self.settings.storage
        writer = PartitionWriter(
            layout.root,
            run_id=run_id,
            scraper=package.name,
            writer_id=LOCAL_WRITER_ID,
            on_commit=self.store.partitions.commit,
            on_seal=self.store.partitions.seal,
            max_rows=storage.partition_max_rows,
            max_bytes=storage.partition_max_bytes,
            fsync=storage.fsync,
        )
        return PartitionSink(
            writer,
            identity=_identity(package),
            batch_rows=storage.batch_rows,
            batch_seconds=storage.batch_seconds,
        )

    def _execute(
        self,
        package: ScraperPackage,
        request: RunRequest,
        layout: RunLayout,
        sink: PartitionSink,
        execution: _Execution,
    ) -> None:
        if request.input_path is not None:
            execution.snapshot = create_input_snapshot(
                request.input_path,
                layout.inputs / f"{package.name}.csv",
                package.definition.inputs or Inputs(),
            )
        with self._open_pipeline(package, self.settings, sink) as pipeline:
            if execution.snapshot is not None:
                execution.result = pipeline.run_rows(list(execution.snapshot.iter_rows()))
            else:
                execution.result = pipeline.run_links(_links(package))
        execution.status = (
            RunStatus.COMPLETED if _succeeded(execution.result) else RunStatus.COMPLETED_WITH_ERRORS
        )

    def _finalize(
        self,
        package: ScraperPackage,
        layout: RunLayout,
        run_id: str,
        started: datetime,
        sink: PartitionSink,
        execution: _Execution,
        events: RunEvents,
    ) -> RunReport:
        try:
            sink.close()
        except Exception as error:
            execution.fail(RunStatus.FAILED, f"saving results failed: {error}")
            logger.exception("Run %s: saving results failed", run_id)
        _emit_failures(events, package.name, execution.result)

        outputs: dict[str, list[Path]] = {}
        try:
            outputs = self._export(package.name, layout, run_id, execution.snapshot)
        except Exception as error:
            execution.fail(RunStatus.FAILED, f"export failed: {error}")
            logger.exception("Run %s: export failed", run_id)

        deliveries = Deliverer(
            base_dir=self.settings.project_root,
            clock=self.store.now,
            record=self.store.deliveries.record,
            sleep=self._sleep,
        ).run(
            self.settings.output.deliveries,
            outputs,
            DeliveryContext(
                run_id=run_id,
                project_id=self.settings.project_id,
                date=f"{started:%Y-%m-%d}",
                succeeded=execution.status.is_completed,
                work_dir=layout.output,
            ),
        )
        for delivery in deliveries:
            events.emit(
                "delivery.finished",
                level=EventLevel.INFO if delivery.error is None else EventLevel.WARNING,
                name=delivery.name,
                status=delivery.status.value,
                location=delivery.location,
                error=delivery.error,
            )

        summary = self._summary(
            package, layout, run_id, started, sink, execution, outputs, deliveries
        )
        layout.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
        run = self.store.runs.finish(
            run_id, execution.status, summary=summary, error=execution.error
        )
        events.emit(
            "run.finished",
            level=EventLevel.INFO if execution.status.is_completed else EventLevel.ERROR,
            scraper=package.name,
            status=execution.status.value,
            rows=sink.count,
            error=execution.error,
        )
        logger.info("Run %s finished: %s, %s rows", run_id, execution.status.value, sink.count)
        return RunReport(run=run, layout=layout, outputs=outputs, deliveries=deliveries)

    def _export(
        self, scraper: str, layout: RunLayout, run_id: str, snapshot: InputSnapshot | None
    ) -> dict[str, list[Path]]:
        output = self.settings.output
        formats = dict.fromkeys(output.formats)
        for delivery in output.deliveries:
            formats.update(dict.fromkeys(delivery.formats or ()))
        outputs: dict[str, list[Path]] = {}
        for fmt in formats:
            path = layout.output / f"{scraper}.{fmt}"
            write_output(
                fmt,
                lambda: scraper_rows(self.store.partitions, layout, run_id, scraper, snapshot),
                path,
            )
            outputs[fmt] = [path]
        if output.archive and outputs:
            files = [path for paths in outputs.values() for path in paths]
            outputs["zip"] = [make_archive(files, layout.output / f"{run_id}.zip")]
        return outputs

    def _summary(
        self,
        package: ScraperPackage,
        layout: RunLayout,
        run_id: str,
        started: datetime,
        sink: PartitionSink,
        execution: _Execution,
        outputs: dict[str, list[Path]],
        deliveries: list[DeliveryRecord],
    ) -> dict[str, Any]:
        finished = self._now()
        snapshot = execution.snapshot
        return {
            "run_id": run_id,
            "project_id": self.settings.project_id,
            "status": execution.status.value,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_seconds": round((finished - started).total_seconds(), 1),
            "error": execution.error,
            "scrapers": {
                package.name: {
                    "version": package.version,
                    "rows": sink.count,
                    "statuses": dict(sink.statuses),
                    "input": None
                    if snapshot is None
                    else {
                        "rows": snapshot.rows,
                        "skipped_invalid": snapshot.skipped_invalid,
                        "skipped_duplicates": snapshot.skipped_duplicates,
                    },
                    "links": _links_summary(execution.result),
                    "outputs": {
                        fmt: [layout.relative(path) for path in paths]
                        for fmt, paths in outputs.items()
                    },
                }
            },
            "deliveries": [
                {
                    "name": delivery.name,
                    "status": delivery.status.value,
                    "location": delivery.location,
                    "error": delivery.error,
                }
                for delivery in deliveries
            ],
        }


@dataclass
class _Execution:
    status: RunStatus = RunStatus.FAILED
    error: str | None = None
    snapshot: InputSnapshot | None = None
    result: PipelineResult | None = None

    def fail(self, status: RunStatus, error: str) -> None:
        if self.status in {RunStatus.FAILED, RunStatus.INTERRUPTED} and self.error:
            return
        self.status = status
        self.error = error


def _identity(package: ScraperPackage) -> ItemIdentity:
    definition = package.definition
    inputs = definition.inputs or Inputs()
    pages = definition.pages
    if pages.detail is not None:
        link_field = pages.detail.link_field
    elif pages.list is not None:
        link_field = pages.list.detail_link_field
    else:
        link_field = inputs.detail_link_field
    return ItemIdentity(fields=inputs.identity_fields, link_field=link_field)


def _links(package: ScraperPackage) -> list[str]:
    groups = package.definition.links
    if any(group.type == "template" for group in groups):
        raise ValueError("template link groups need a workload with page ranges")
    links = [url for group in groups for url in group.urls]
    if not links:
        raise ValueError(f"{package.name} has no links; pass an input file with detail rows")
    return links


def _succeeded(result: PipelineResult) -> bool:
    links_ok = all(link.category is None for link in result.links)
    details = [*result.detail_results, *(d for link in result.links for d in link.detail_results)]
    return links_ok and all(detail.ok for detail in details)


def _emit_failures(events: RunEvents, scraper: str, result: PipelineResult | None) -> None:
    if result is None:
        return
    failures: list[dict[str, Any]] = []
    for link in result.links:
        if link.category is not None:
            failures.append({"kind": "link", "url": link.link, **_outcome(link.outcome)})
            failures[-1]["category"] = link.category.value
    details = [*result.detail_results, *(d for link in result.links for d in link.detail_results)]
    for detail in details:
        if detail.category is not None:
            failures.append(
                {
                    "kind": "detail",
                    "category": detail.category.value,
                    "missing": list(detail.missing),
                    **_outcome(detail.outcome),
                }
            )
    for failure in failures[:MAX_FAILURE_EVENTS]:
        level = EventLevel.INFO if failure["category"] == "not_found" else EventLevel.WARNING
        events.emit("item.failed", level=level, scraper=scraper, **failure)


def _outcome(outcome: PageOutcome | None) -> dict[str, Any]:
    if outcome is None:
        return {}
    return {
        "url": outcome.url,
        "state": outcome.state.value,
        "reason": outcome.reason,
        "http_status": outcome.http_status,
    }


def _links_summary(result: PipelineResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    summary = []
    for link in result.links:
        listing = link.list_result
        summary.append(
            {
                "link": link.link,
                "items": len(listing.items) if listing else 0,
                "pages": [visit.items_found for visit in listing.visits] if listing else [],
                "stop_reason": listing.stop_reason if listing else None,
                "state": link.outcome.state.value if link.outcome else None,
                "category": link.category.value if link.category else None,
            }
        )
    return summary

"""Entry point and task loop of a slot process: one browser session, many tasks."""

from __future__ import annotations

import csv
import importlib
import logging
import os
import signal
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from dailyscraper.browsers.channel import SlotChannel, TaskCancelled
from dailyscraper.browsers.messages import (
    RunTask,
    SessionBroken,
    Shutdown,
    SlotFailed,
    SlotReady,
    TaskFinished,
    TaskStarted,
)
from dailyscraper.detection import classify_error
from dailyscraper.domain import AttemptStatus, ErrorCategory, TaskKind, TaskOutcome
from dailyscraper.execution.partitions import ItemIdentity, PartitionSink, PartitionWriter
from dailyscraper.execution.snapshots import SnapshotWriter
from dailyscraper.execution.timing import Pacer
from dailyscraper.pipelines import DefaultPipeline, PipelineResult
from dailyscraper.schema import ScraperDefinition, ScraperPackage
from dailyscraper.settings import Settings
from dailyscraper.tasks.inputs import SNAPSHOT_DELIMITER, SNAPSHOT_ENCODING
from dailyscraper.tasks.retry import RETRYABLE
from dailyscraper.transport import BrowserClosedError, is_browser_closed_error

logger = logging.getLogger(__name__)

DEFAULT_OPENER = "dailyscraper.execution.session:open_pipeline"
MAX_FAILURES_PER_TASK = 50

PipelineOpener = Callable[..., AbstractContextManager[DefaultPipeline]]


class SlotConfig(BaseModel):
    """Everything a slot needs; it reads no configuration from disk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_id: str
    run_id: str
    run_root: Path
    settings: dict[str, Any]
    package_root: Path
    package_version: str
    definition: dict[str, Any]
    identity_fields: tuple[str, ...] = ()
    link_field: str | None = None
    input_snapshot: Path | None = None
    opener: str = DEFAULT_OPENER


def slot_main(config_json: str, connection: Connection) -> None:
    # The parent decides when slots stop; Ctrl+C in the shared console must not kill them.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    config = SlotConfig.model_validate_json(config_json)
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s [{config.slot_id}] %(name)s: %(message)s",
    )
    settings = Settings.model_validate(config.settings)
    channel = SlotChannel(connection, ack_timeout=settings.runtime.commit_ack_timeout_seconds)
    package = ScraperPackage(
        root=config.package_root,
        definition=ScraperDefinition.model_validate(config.definition),
        version=config.package_version,
    )
    storage = settings.storage
    sink = PartitionSink(
        PartitionWriter(
            config.run_root,
            run_id=config.run_id,
            scraper=package.name,
            writer_id=config.slot_id,
            on_commit=channel.commit,
            on_seal=channel.seal,
            max_rows=storage.partition_max_rows,
            max_bytes=storage.partition_max_bytes,
            fsync=storage.fsync,
        ),
        identity=ItemIdentity(fields=config.identity_fields, link_field=config.link_field),
        batch_rows=storage.batch_rows,
        batch_seconds=storage.batch_seconds,
    )
    snapshots = SnapshotWriter(
        config.run_root / "snapshots" / package.name,
        limit=settings.runtime.snapshots_per_slot,
        prefix=f"{config.slot_id}-",
    )
    opener = _import(config.opener)
    try:
        with opener(
            package, settings, sink, pacer=Pacer(channel.check_interrupted), snapshots=snapshots
        ) as pipeline:
            channel.send(SlotReady(pid=os.getpid()))
            _serve(channel, pipeline, sink, config)
        sink.close()
    except Exception as error:
        logger.exception("Slot %s stopped", config.slot_id)
        message = f"{type(error).__name__}: {error}"
        if is_browser_closed_error(error):
            channel.send(SessionBroken(reason=message))
        else:
            channel.send(SlotFailed(error=message))
    finally:
        connection.close()


def _serve(
    channel: SlotChannel, pipeline: DefaultPipeline, sink: PartitionSink, config: SlotConfig
) -> None:
    while True:
        message = channel.receive(timeout=None)
        if message is None or isinstance(message, Shutdown):
            return
        if not isinstance(message, RunTask):
            continue
        channel.attempt_id = message.attempt_id
        channel.cancelled = False
        sink.start_attempt(message.task_id, message.attempt_id)
        channel.send(TaskStarted(attempt_id=message.attempt_id))
        outcome = run_task(pipeline, message, config)
        try:
            sink.flush()
        except Exception as error:
            outcome = TaskOutcome(
                AttemptStatus.FAILED,
                category=classify_error(error),
                rows=outcome.rows,
                error=f"saving rows failed: {error}",
            )
        channel.send(TaskFinished(attempt_id=message.attempt_id, outcome=outcome))
        if outcome.category is ErrorCategory.BROWSER_CLOSED:
            raise BrowserClosedError(f"{message.attempt_id}: {outcome.error}")
        if channel.shutdown_requested:
            return


def run_task(pipeline: DefaultPipeline, task: RunTask, config: SlotConfig) -> TaskOutcome:
    try:
        if task.kind is TaskKind.LIST:
            return _list_outcome(pipeline.run_links([task.payload["link"]]))
        positions = [int(row) for row in task.payload["rows"]]
        rows = read_snapshot_rows(config.input_snapshot, positions)
        result = pipeline.run_rows(rows, positions=positions)
        return _detail_outcome(result, positions)
    except TaskCancelled:
        return TaskOutcome(AttemptStatus.CANCELLED, category=ErrorCategory.CANCELLED)
    except Exception as error:
        logger.exception("Task %s failed", task.attempt_id)
        return TaskOutcome(
            AttemptStatus.FAILED,
            category=classify_error(error),
            error=f"{type(error).__name__}: {error}",
        )


def read_snapshot_rows(path: Path | None, positions: list[int]) -> list[dict[str, str]]:
    if path is None:
        raise ValueError("detail task without an input snapshot")
    wanted = set(positions)
    found: dict[int, dict[str, str]] = {}
    with Path(path).open(encoding=SNAPSHOT_ENCODING, newline="") as stream:
        for position, row in enumerate(csv.DictReader(stream, delimiter=SNAPSHOT_DELIMITER), 1):
            if position in wanted:
                found[position] = dict(row)
    missing = wanted - set(found)
    if missing:
        raise ValueError(f"input snapshot has no rows {sorted(missing)}")
    return [found[position] for position in positions]


def _list_outcome(result: PipelineResult) -> TaskOutcome:
    (link,) = result.links
    listing = link.list_result
    details = {
        "link": link.link,
        "items": len(listing.items) if listing else 0,
        "pages": [visit.items_found for visit in listing.visits] if listing else [],
        "stop_reason": listing.stop_reason if listing else None,
        "state": link.outcome.state.value if link.outcome else None,
    }
    failures = _failures(link.detail_results)
    details["failures"] = _count_errors(failures) + (1 if link.category is not None else 0)
    if link.category is not None:
        failures.insert(0, {"kind": "link", "category": link.category.value, **_page(link.outcome)})
        return TaskOutcome(
            AttemptStatus.FAILED,
            category=link.category,
            rows=result.emitted,
            error=link.outcome.reason if link.outcome else None,
            failures=tuple(failures[:MAX_FAILURES_PER_TASK]),
            details=details,
        )
    return TaskOutcome(
        AttemptStatus.COMPLETED,
        rows=result.emitted,
        failures=tuple(failures[:MAX_FAILURES_PER_TASK]),
        details=details,
    )


def _detail_outcome(result: PipelineResult, positions: list[int]) -> TaskOutcome:
    retry_rows = tuple(
        position
        for position, detail in zip(positions, result.detail_results, strict=True)
        if detail.category in RETRYABLE
    )
    failures = _failures(result.detail_results)
    return TaskOutcome(
        AttemptStatus.COMPLETED,
        rows=result.emitted,
        retry_rows=retry_rows,
        failures=tuple(failures[:MAX_FAILURES_PER_TASK]),
        details={"rows": len(positions), "failures": _count_errors(failures)},
    )


def _count_errors(failures: list[dict[str, Any]]) -> int:
    return sum(1 for failure in failures if failure["category"] != ErrorCategory.NOT_FOUND)


def _failures(details: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "detail",
            "category": detail.category.value,
            "missing": list(detail.missing),
            **_page(detail.outcome),
        }
        for detail in details
        if detail.category is not None
    ]


def _page(outcome: Any) -> Mapping[str, Any]:
    if outcome is None:
        return {}
    return {
        "url": outcome.url,
        "state": outcome.state.value,
        "reason": outcome.reason,
        "http_status": outcome.http_status,
    }


def _import(path: str) -> PipelineOpener:
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)

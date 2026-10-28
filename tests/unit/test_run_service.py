from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from dailyscraper.app import cli
from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import DeliveryStatus, ErrorCategory, RunStatus
from dailyscraper.output import iter_csv
from dailyscraper.page_scrapers import ListResult
from dailyscraper.pipelines import LinkResult, PipelineResult
from dailyscraper.settings import load_settings
from dailyscraper.settings.models import DeliverySettings, OutputSettings, StorageSettings
from dailyscraper.storage import open_store


@pytest.fixture
def settings(project_root: Path, scenario_workspace: Path):
    loaded = load_settings(project_root)
    paths = loaded.paths.model_copy(update={"data": scenario_workspace / "data"})
    return loaded.model_copy(
        update={
            "paths": paths,
            "storage": StorageSettings(batch_rows=2, fsync=False),
            "output": OutputSettings(formats=("csv", "json")),
        }
    )


@pytest.fixture
def store(settings):
    opened = open_store("sqlite", settings.database_path)
    yield opened
    opened.close()


class FakePipeline:
    def __init__(self, sink, behaviour) -> None:
        self.sink = sink
        self.behaviour = behaviour

    def run_links(self, links):
        return self.behaviour(self.sink, links=links)

    def run_rows(self, rows):
        return self.behaviour(self.sink, rows=rows)


def opener(behaviour):
    @contextmanager
    def open_fake(package, settings, sink):
        yield FakePipeline(sink, behaviour)

    return open_fake


def emit_catalog(sink, links):
    items = [{"item_name": f"p{i}", "detail_link": f"https://a/p{i}"} for i in range(3)]
    for item in items:
        sink.emit(item)
    return PipelineResult(
        links=[LinkResult(links[0], list_result=ListResult(items=items, stop_reason="last_page"))],
        emitted=3,
    )


def test_completed_run_is_stored_exported_and_logged(settings, store) -> None:
    report = RunService(settings, store, pipeline_opener=opener(emit_catalog)).run(
        RunRequest("controlled_catalog")
    )

    run = store.runs.get(report.run.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.packages["controlled_catalog"] == report.run.packages["controlled_catalog"]
    assert run.summary["scrapers"]["controlled_catalog"]["rows"] == 3
    assert [row["item_name"] for row in iter_csv(report.outputs["csv"][0])] == ["p0", "p1", "p2"]
    assert len(json.loads(report.outputs["json"][0].read_text(encoding="utf-8"))) == 3
    assert json.loads(report.layout.summary.read_text(encoding="utf-8"))["status"] == "completed"
    assert report.layout.config.is_file()
    types = [event.type for event in store.events.list(run.run_id)]
    assert types == ["run.started", "run.finished"]


def test_failed_run_keeps_committed_rows_and_delivers_only_when_asked(settings, store) -> None:
    def fail_midway(sink, links):
        sink.emit({"item_name": "a"})
        sink.emit({"item_name": "b"})
        sink.emit({"item_name": "pending"})
        raise RuntimeError("browser crashed")

    deliveries = (
        DeliverySettings(name="on_success", type="folder", path=str(settings.paths.data / "ok")),
        DeliverySettings(
            name="always", type="folder", path=str(settings.paths.data / "all"), when="always"
        ),
    )
    settings = settings.model_copy(
        update={"output": settings.output.model_copy(update={"deliveries": deliveries})}
    )

    report = RunService(settings, store, pipeline_opener=opener(fail_midway)).run(
        RunRequest("controlled_catalog")
    )

    assert report.run.status is RunStatus.FAILED
    assert "browser crashed" in report.run.error
    rows = list(iter_csv(report.outputs["csv"][0]))
    assert [row["item_name"] for row in rows] == ["a", "b", "pending"]
    statuses = {delivery.name: delivery.status for delivery in report.deliveries}
    assert statuses == {"on_success": DeliveryStatus.SKIPPED, "always": DeliveryStatus.DELIVERED}
    assert (settings.paths.data / "all" / "controlled_catalog.csv").is_file()
    assert [d.name for d in store.deliveries.list(report.run.run_id)] == ["on_success", "always"]


def test_interrupted_run_is_finalized(settings, store) -> None:
    def interrupt(sink, links):
        sink.emit({"item_name": "a"})
        raise KeyboardInterrupt

    report = RunService(settings, store, pipeline_opener=opener(interrupt)).run(
        RunRequest("controlled_catalog")
    )

    assert report.run.status is RunStatus.INTERRUPTED
    assert store.runs.get(report.run.run_id).finished_at is not None
    assert len(list(iter_csv(report.outputs["csv"][0]))) == 1


def test_failed_link_completes_with_errors(settings, store) -> None:
    def blocked(sink, links):
        return PipelineResult(links=[LinkResult(links[0], category=ErrorCategory.BLOCKED)])

    report = RunService(settings, store, pipeline_opener=opener(blocked)).run(
        RunRequest("controlled_catalog")
    )

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    (link,) = report.run.summary["scrapers"]["controlled_catalog"]["links"]
    assert link["category"] == "blocked"
    (failure,) = store.events.list(report.run.run_id, types=["item.failed"])
    assert failure.data["kind"] == "link" and failure.data["category"] == "blocked"


def test_input_run_exports_rows_in_input_order(settings, store, scenario_workspace: Path) -> None:
    source = scenario_workspace / "input.csv"
    source.write_text(
        "source_id;detail_link\nA;https://a/1\nB;https://a/2\nC;\n", encoding="utf-8-sig"
    )

    def details(sink, rows):
        sink.emit({**rows[1], "title": "second"}, input_row=2)
        sink.emit({**rows[0], "title": "first"}, input_row=1, status="not_found")
        return PipelineResult()

    report = RunService(settings, store, pipeline_opener=opener(details)).run(
        RunRequest("controlled_details", source)
    )

    rows = list(iter_csv(report.outputs["csv"][0]))
    assert [(row["source_id"], row["title"]) for row in rows] == [("A", "first"), ("B", "second")]
    summary = report.run.summary["scrapers"]["controlled_details"]
    assert summary["input"] == {"rows": 2, "skipped_invalid": 1, "skipped_duplicates": 0}
    assert summary["statuses"] == {"ok": 1, "not_found": 1}


def test_invalid_input_fails_the_run(settings, store, scenario_workspace: Path) -> None:
    source = scenario_workspace / "input.csv"
    source.write_text("source_id\nA\n", encoding="utf-8")

    report = RunService(settings, store, pipeline_opener=opener(emit_catalog)).run(
        RunRequest("controlled_details", source)
    )

    assert report.run.status is RunStatus.FAILED
    assert "detail_link" in report.run.error


def test_cli_lists_and_shows_runs(settings, store, monkeypatch, capsys) -> None:
    report = RunService(settings, store, pipeline_opener=opener(emit_catalog)).run(
        RunRequest("controlled_catalog")
    )
    monkeypatch.setattr(cli, "load_settings", lambda root: settings)

    assert cli.main(["runs"]) == 0
    listing = capsys.readouterr().out
    assert report.run.run_id in listing and "completed" in listing

    assert cli.main(["runs", "show", report.run.run_id]) == 0
    assert '"rows": 3' in capsys.readouterr().out
    assert cli.main(["runs", "show", "missing"]) == 1

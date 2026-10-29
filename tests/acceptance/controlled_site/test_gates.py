"""The controlled catalog with a health probe and discovery in real Chromium."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import RunStatus, TaskKind
from dailyscraper.output import iter_csv
from dailyscraper.settings import load_settings
from dailyscraper.storage import open_store

pytestmark = pytest.mark.browser


def prepared(project_root: Path, scenario_workspace: Path, *, save_items: bool = True):
    loaded = load_settings(project_root)
    settings = loaded.model_copy(
        update={
            "paths": loaded.paths.model_copy(
                update={
                    "data": scenario_workspace / "data",
                    "scrapers": scenario_workspace / "scrapers",
                }
            ),
            "runtime": loaded.runtime.model_copy(update={"pages_per_task": 1}),
        }
    )
    source = project_root / "tests" / "scrapers" / "controlled_catalog" / "scraper.json"
    definition = json.loads(source.read_text("utf-8"))
    definition["name"] = "discovered_catalog"
    definition["pages"]["list"]["pagination"]["last_page"] = 0
    definition["discovery"] = {
        "enabled": True,
        "probe_wait_seconds": 2,
        "unknown_retries": 0,
        "save_items": save_items,
    }
    definition["health_check"] = {
        "blocking": True,
        "min_items": 3,
        "required_fields": ["item_name", "price"],
    }
    target = settings.paths.scrapers / "discovered_catalog"
    target.mkdir(parents=True)
    (target / "scraper.json").write_text(json.dumps(definition), encoding="utf-8")
    store = open_store("sqlite", settings.database_path)
    return settings, store


def test_discovery_reads_a_short_catalog_by_itself(
    project_root: Path, scenario_workspace: Path, fixtures_root: Path
) -> None:
    dataset = json.loads(
        (fixtures_root / "expected" / "controlled_dataset_v1.json").read_text("utf-8")
    )
    settings, store = prepared(project_root, scenario_workspace)

    report = RunService(settings, store).run(RunRequest(("discovered_catalog",)))

    assert report.run.status is RunStatus.COMPLETED, report.run.error
    rows = list(iter_csv(report.outputs["csv"][0]))
    assert len(rows) == dataset["products_count"]
    pages = len(dataset["catalog_page_counts"])
    summary = report.run.summary["scrapers"]["discovered_catalog"]
    assert summary["gate"]["state"] == "open"
    (found,) = summary["discovery"]
    assert (found["status"], found["last_page"]) == ("ok", pages)
    assert found["saved_pages"] == list(range(1, pages + 1))
    kinds = [task.kind for task in store.tasks.list(report.run.run_id)]
    assert kinds == [TaskKind.PROBE, TaskKind.DISCOVER]
    assert {row["item_name"] for row in rows} >= {
        product["name"] for product in dataset["stable_products"]
    }


def test_without_saved_items_list_tasks_read_every_page(
    project_root: Path, scenario_workspace: Path, fixtures_root: Path
) -> None:
    dataset = json.loads(
        (fixtures_root / "expected" / "controlled_dataset_v1.json").read_text("utf-8")
    )
    settings, store = prepared(project_root, scenario_workspace, save_items=False)

    report = RunService(settings, store).run(RunRequest(("discovered_catalog",)))

    assert report.run.status is RunStatus.COMPLETED, report.run.error
    assert len(list(iter_csv(report.outputs["csv"][0]))) == dataset["products_count"]
    ranges = [
        task.payload["pages"]
        for task in store.tasks.list(report.run.run_id)
        if task.kind is TaskKind.LIST
    ]
    assert sorted(ranges) == [[1, 1], [2, 2], [3, 3]]

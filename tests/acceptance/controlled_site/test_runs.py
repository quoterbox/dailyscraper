from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import RunStatus
from dailyscraper.output import iter_csv
from dailyscraper.settings import load_settings
from dailyscraper.settings.models import ScraperSelection
from dailyscraper.storage import open_store

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def settings(project_root: Path):
    return load_settings(project_root)


@pytest.fixture(scope="module")
def store(settings):
    opened = open_store(settings.storage.backend, settings.database_path)
    yield opened
    opened.close()


def expected(fixtures_root: Path, name: str) -> dict:
    return json.loads((fixtures_root / "expected" / name).read_text(encoding="utf-8"))


def test_catalog_run_is_stored_and_exported(settings, store, fixtures_root: Path) -> None:
    dataset = expected(fixtures_root, "controlled_dataset_v1.json")

    report = RunService(settings, store).run(RunRequest(("controlled_catalog",)))

    assert report.run.status is RunStatus.COMPLETED
    rows = list(iter_csv(report.outputs["csv"][0]))
    assert len(rows) == dataset["products_count"]
    assert {row["item_name"] for row in rows} >= {
        product["name"] for product in dataset["stable_products"]
    }
    summary = store.runs.get(report.run.run_id).summary
    (link,) = summary["scrapers"]["controlled_catalog"]["links"]
    assert link["pages"] == dataset["catalog_page_counts"]
    statuses = summary["scrapers"]["controlled_catalog"]["statuses"]
    assert statuses == {"ok": dataset["products_count"]}
    types = [event.type for event in store.events.list(report.run.run_id)]
    assert types[0] == "run.started" and types[-1] == "run.finished"


def test_detail_run_keeps_input_order_and_missing_products(
    settings, store, fixtures_root: Path, scenario_workspace: Path
) -> None:
    contract = expected(fixtures_root, "controlled_details_v1.json")
    inputs = fixtures_root / "input"
    found = (inputs / "controlled_details_v1.csv").read_text(encoding="utf-8-sig").splitlines()
    missing = (inputs / "controlled_details_missing_v1.csv").read_text(encoding="utf-8-sig")
    combined = scenario_workspace / "details.csv"
    combined.write_text("\n".join([*found, *missing.splitlines()[1:]]) + "\n", encoding="utf-8")

    report = RunService(settings, store).run(RunRequest(("controlled_details",), combined))

    assert report.run.status is RunStatus.COMPLETED
    rows = list(iter_csv(report.outputs["csv"][0]))
    normal = {key: str(value) for key, value in contract["normal_product"].items()}
    assert {key: rows[0][key] for key in normal} == normal
    assert rows[1]["detail_link"] == contract["missing_product"]["detail_link"]
    statuses = report.run.summary["scrapers"]["controlled_details"]["statuses"]
    assert statuses == {"ok": 1, "not_found": 1}


def test_detail_rows_run_in_parallel_browsers(
    settings, store, fixtures_root: Path, scenario_workspace: Path
) -> None:
    contract = expected(fixtures_root, "controlled_details_v1.json")
    normal = contract["normal_product"]
    combined = scenario_workspace / "details.csv"
    lines = ["source_id;item_name;detail_link"] + [
        f"copy{n};{normal['item_name']};{normal['detail_link']}" for n in range(1, 5)
    ]
    combined.write_text("\n".join(lines) + "\n", encoding="utf-8")
    parallel = settings.model_copy(
        update={
            "scrapers": (ScraperSelection(name="controlled_details", max_parallel=2),),
            "runtime": settings.runtime.model_copy(
                update={"items_per_task": 1, "max_browsers_per_site_proxy": 2}
            ),
        }
    )

    report = RunService(parallel, store).run(RunRequest(("controlled_details",), combined))

    assert report.run.status is RunStatus.COMPLETED
    assert report.run.summary["slots"] == 2
    rows = list(iter_csv(report.outputs["csv"][0]))
    assert [row["source_id"] for row in rows] == ["copy1", "copy2", "copy3", "copy4"]
    assert {row["sku"] for row in rows} == {normal["sku"]}

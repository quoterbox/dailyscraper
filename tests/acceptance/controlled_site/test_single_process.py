from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.app.session import open_pipeline
from dailyscraper.app.single import read_rows
from dailyscraper.domain import ErrorCategory, PageState
from dailyscraper.execution.sink import MemorySink
from dailyscraper.schema import Inputs, load_scraper_package
from dailyscraper.settings import load_settings

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def settings(project_root: Path):
    return load_settings(project_root)


def load(settings, name: str):
    return load_scraper_package(settings.paths.scrapers / name, variables={})


def expected(fixtures_root: Path, name: str) -> dict:
    return json.loads((fixtures_root / "expected" / name).read_text(encoding="utf-8"))


def test_catalog_pages_and_products_match_the_dataset(settings, fixtures_root: Path) -> None:
    dataset = expected(fixtures_root, "controlled_dataset_v1.json")
    package = load(settings, "controlled_catalog")
    sink = MemorySink()

    with open_pipeline(package, settings, sink) as pipeline:
        result = pipeline.run_links([dataset["base_url"]])

    (link,) = result.links
    assert link.category is None
    assert [visit.items_found for visit in link.list_result.visits] == dataset[
        "catalog_page_counts"
    ]
    assert link.list_result.stop_reason == "last_page"
    slugs = {item["detail_link"].rstrip("/").rsplit("/", 1)[-1] for item in sink.items}
    assert len(sink.items) == dataset["products_count"] == len(slugs)
    assert {product["slug"] for product in dataset["stable_products"]} <= slugs
    assert all(item["start_link"] == dataset["base_url"] for item in sink.items)
    assert {item["item_name"] for item in sink.items} >= {
        product["name"] for product in dataset["stable_products"]
    }


def test_detail_rows_are_enriched_and_missing_pages_are_not_found(
    settings, fixtures_root: Path
) -> None:
    contract = expected(fixtures_root, "controlled_details_v1.json")
    package = load(settings, "controlled_details")
    inputs = fixtures_root / "input"
    rows = [
        *read_rows(inputs / "controlled_details_v1.csv", Inputs()),
        *read_rows(inputs / "controlled_details_missing_v1.csv", Inputs()),
    ]
    sink = MemorySink()

    with open_pipeline(package, settings, sink) as pipeline:
        result = pipeline.run_rows(rows)

    found, missing = result.detail_results
    assert found.category is None
    assert found.row == contract["normal_product"]
    assert missing.category is ErrorCategory.NOT_FOUND
    assert missing.outcome.state is PageState.NOT_FOUND
    assert missing.row == contract["missing_product"]
    assert sink.items == [contract["normal_product"], contract["missing_product"]]

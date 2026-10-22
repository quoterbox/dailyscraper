"""The controlled site still serves the data contract the acceptance tests rely on."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.network

PRODUCT_LINK = re.compile(r'href="(?P<base>[^"]+?/product/)(?P<slug>[^/"]+)/"')
USER_AGENT = "Mozilla/5.0 (dailyscraper controlled-site contract check)"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        return response.read().decode("utf-8", errors="replace")


def page_url(base_url: str, page: int) -> str:
    return base_url if page == 1 else f"{base_url}page/{page}/"


def test_catalog_pages_match_the_controlled_dataset(fixtures_root: Path) -> None:
    dataset = json.loads(
        (fixtures_root / "expected" / "controlled_dataset_v1.json").read_text(encoding="utf-8")
    )
    base_url = dataset["base_url"]

    slugs_by_page = [
        {match["slug"] for match in PRODUCT_LINK.finditer(fetch(page_url(base_url, page)))}
        for page in range(1, dataset["catalog_pages"] + 1)
    ]

    assert [len(slugs) for slugs in slugs_by_page] == dataset["catalog_page_counts"]
    all_slugs = set().union(*slugs_by_page)
    assert len(all_slugs) == dataset["products_count"]
    assert {product["slug"] for product in dataset["stable_products"]} <= all_slugs

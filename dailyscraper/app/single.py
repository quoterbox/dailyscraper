"""Run one scraper package in one browser, without a queue or proxies.

uv run python -m dailyscraper.app.single SCRAPER [--input ROWS.csv] [--output PATH] [--headed]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from dailyscraper.app.session import open_pipeline
from dailyscraper.execution.sink import JsonLinesSink
from dailyscraper.pipelines import PipelineResult
from dailyscraper.schema import Inputs, ScraperPackage, load_scraper_package
from dailyscraper.settings import Settings, load_settings, load_variables
from dailyscraper.settings.environment import resolve_environment_paths

logger = logging.getLogger("dailyscraper")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    root = Path.cwd()
    settings = load_settings(root)
    if args.headed:
        settings = _headed(settings)
    package = load_scraper_package(
        settings.paths.scrapers / args.scraper,
        variables=load_variables(resolve_environment_paths(root)),
    )
    output = args.output or _default_output(settings, package)

    with JsonLinesSink(output) as sink, open_pipeline(package, settings, sink) as pipeline:
        if args.input:
            result = pipeline.run_rows(read_rows(args.input, package.definition.inputs or Inputs()))
        else:
            result = pipeline.run_links(_links(package))

    csv_path = write_csv(output, output.with_suffix(".csv"))
    _report(result, output, csv_path)
    return 0 if _succeeded(result) else 1


def read_rows(path: Path, inputs: Inputs) -> list[dict[str, Any]]:
    with path.open(encoding=inputs.encoding, newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream, delimiter=inputs.delimiter)]


def write_csv(jsonl: Path, target: Path) -> Path:
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return target


def _links(package: ScraperPackage) -> list[str]:
    groups = package.definition.links
    if any(group.type == "template" for group in groups):
        raise SystemExit("template link groups need a workload with page ranges (stage 3)")
    links = [url for group in groups for url in group.urls]
    if not links:
        raise SystemExit(f"{package.name} has no links; pass --input for detail rows")
    return links


def _headed(settings: Settings) -> Settings:
    playwright = settings.browser.playwright.model_copy(update={"headless": False})
    browser = settings.browser.model_copy(update={"playwright": playwright})
    return settings.model_copy(update={"browser": browser})


def _default_output(settings: Settings, package: ScraperPackage) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return settings.paths.data / "single" / package.name / f"{stamp}.jsonl"


def _succeeded(result: PipelineResult) -> bool:
    links_ok = all(link.category is None for link in result.links)
    details = [*result.detail_results, *(d for link in result.links for d in link.detail_results)]
    return links_ok and all(detail.ok for detail in details)


def _report(result: PipelineResult, output: Path, csv_path: Path) -> None:
    for link in result.links:
        visits = link.list_result.visits if link.list_result else []
        logger.info(
            "Link %s: items=%s pages=%s stop=%s state=%s",
            link.link,
            len(link.list_result.items) if link.list_result else 0,
            [visit.items_found for visit in visits],
            link.list_result.stop_reason if link.list_result else "-",
            link.outcome.state.value if link.outcome else "-",
        )
    details = [*result.detail_results, *(d for link in result.links for d in link.detail_results)]
    if details:
        outcomes = Counter(d.category.value if d.category else "ok" for d in details)
        logger.info("Detail rows: %s", dict(outcomes))
    logger.info("Written %s rows to %s and %s", result.emitted, output, csv_path)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one scraper package in one browser.")
    parser.add_argument("scraper")
    parser.add_argument("--input", type=Path, help="CSV with detail rows instead of the links")
    parser.add_argument(
        "--output", type=Path, help="NDJSON output path; a CSV is written next to it"
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

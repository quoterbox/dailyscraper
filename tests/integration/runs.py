"""Test packages, inputs and runs with the browserless pipeline from ``tests.fakes``."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.output import iter_csv
from tests.fakes import OPENER


def make_package(
    settings,
    project_root: Path,
    name: str,
    kind: str,
    fake: dict,
    edit: Callable[[dict], None] | None = None,
) -> str:
    source = "controlled_catalog" if kind == "list" else "controlled_details"
    definition = json.loads(
        (project_root / "tests" / "scrapers" / source / "scraper.json").read_text("utf-8")
    )
    definition["name"] = name
    definition["options"] = {"fake": fake}
    if kind == "list":
        definition["links"] = [
            {"id": "main", "type": "list", "urls": ["https://a.test/c1", "https://a.test/c2"]}
        ]
    if edit is not None:
        edit(definition)
    target = settings.paths.scrapers / name
    target.mkdir(parents=True)
    (target / "scraper.json").write_text(json.dumps(definition), encoding="utf-8")
    return name


def make_input(path: Path, rows: int) -> Path:
    lines = ["source_id;detail_link"] + [f"s{n};https://a.test/p{n}" for n in range(1, rows + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(settings, store, *scrapers: str, input_path: Path | None = None):
    return RunService(settings, store, opener=OPENER).run(RunRequest(scrapers, input_path))


def output_rows(report, name: str) -> list[dict[str, str]]:
    (path,) = [path for path in report.outputs["csv"] if path.stem == name]
    return list(iter_csv(path))

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import RunStatus
from dailyscraper.settings import Settings, load_settings
from dailyscraper.storage import RunLayout, Store, open_store

EXIT_CODES = {
    RunStatus.COMPLETED: 0,
    RunStatus.COMPLETED_WITH_ERRORS: 1,
    RunStatus.FAILED: 1,
    RunStatus.INTERRUPTED: 130,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings(Path.cwd())
    if getattr(args, "headed", False):
        settings = _headed(settings)
    store = open_store(
        settings.storage.backend,
        settings.database_path,
        busy_timeout_ms=settings.storage.busy_timeout_ms,
    )
    try:
        if args.command == "run":
            return _run(settings, store, args)
        if args.runs_command == "show":
            return _show(settings, store, args.run_id)
        return _list(store, args.limit, args.project)
    finally:
        store.close()


def _run(settings: Settings, store: Store, args: argparse.Namespace) -> int:
    report = RunService(settings, store).run(RunRequest(args.scraper, args.input))
    run = report.run
    print(f"\nRun {run.run_id}: {run.status.value}")
    if run.error:
        print(f"Error: {run.error}")
    for name, scraper in (run.summary or {}).get("scrapers", {}).items():
        print(f"{name}: {scraper['rows']} rows {scraper['statuses']}")
    for paths in report.outputs.values():
        for path in paths:
            print(f"  {path}")
    for delivery in report.deliveries:
        target = delivery.location or delivery.error or ""
        print(f"Delivery {delivery.name}: {delivery.status.value} {target}")
    return EXIT_CODES[run.status]


def _list(store: Store, limit: int, project: str | None) -> int:
    runs = store.runs.list(project_id=project, limit=limit)
    if not runs:
        print("No runs yet.")
        return 0
    print(f"{'RUN':<24} {'STATUS':<22} {'ROWS':>6}  {'STARTED':<30} SCRAPERS")
    for run in runs:
        scrapers = (run.summary or {}).get("scrapers", {})
        rows = sum(scraper.get("rows", 0) for scraper in scrapers.values())
        shown_rows = rows if run.summary else "-"
        print(
            f"{run.run_id:<24} {run.status.value:<22} {shown_rows:>6}  "
            f"{run.started_at:<30} {', '.join(run.scrapers)}"
        )
    return 0


def _show(settings: Settings, store: Store, run_id: str) -> int:
    run = store.runs.get(run_id)
    if run is None:
        print(f"Run {run_id} not found.", file=sys.stderr)
        return 1
    layout = RunLayout.for_run(settings.paths.data, run_id)
    print(f"Run {run.run_id}: {run.status.value}")
    print(f"Started {run.started_at}, finished {run.finished_at or '-'}")
    print(f"Directory: {layout.root}")
    if run.error:
        print(f"Error: {run.error}")
    if run.summary:
        print(json.dumps(dict(run.summary), ensure_ascii=False, indent=2))
    return 0


def _headed(settings: Settings) -> Settings:
    playwright = settings.browser.playwright.model_copy(update={"headless": False})
    browser = settings.browser.model_copy(update={"playwright": playwright})
    return settings.model_copy(update={"browser": browser})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dailyscraper")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one scraper package")
    run.add_argument("scraper")
    run.add_argument("--input", type=Path, help="CSV with detail rows instead of the links")
    run.add_argument("--headed", action="store_true", help="show the browser window")

    runs = commands.add_parser("runs", help="history of runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--project")
    runs_commands = runs.add_subparsers(dest="runs_command")
    show = runs_commands.add_parser("show", help="details of one run")
    show.add_argument("run_id")
    return parser


if __name__ == "__main__":
    sys.exit(main())

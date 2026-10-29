from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import RunStatus
from dailyscraper.observability.notifications.channels import (
    Message,
    Sent,
    build_channels,
)
from dailyscraper.settings import Settings, load_settings, load_variables
from dailyscraper.settings.environment import resolve_environment_paths
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
    if args.command == "notifications":
        return _test_notifications(settings)
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
        if args.command == "stop":
            return _stop(settings, store, args.run_id, kill=args.kill)
        RunService(settings, store).reconcile_orphans()
        if args.runs_command == "show":
            return _show(settings, store, args.run_id)
        return _list(store, args.limit, args.project)
    finally:
        store.close()


def _run(settings: Settings, store: Store, args: argparse.Namespace) -> int:
    report = RunService(settings, store).run(RunRequest(tuple(args.scrapers), args.input))
    run = report.run
    print(f"\nRun {run.run_id}: {run.status.value}")
    if run.error:
        print(f"Error: {run.error}")
    for name, scraper in (run.summary or {}).get("scrapers", {}).items():
        print(f"{name}: {scraper['rows']} rows {scraper['statuses']} tasks {scraper['tasks']}")
    for paths in report.outputs.values():
        for path in paths:
            print(f"  {path}")
    for delivery in report.deliveries:
        target = delivery.location or delivery.error or ""
        print(f"Delivery {delivery.name}: {delivery.status.value} {target}")
    return EXIT_CODES[run.status]


def _stop(settings: Settings, store: Store, run_id: str, *, kill: bool) -> int:
    run = store.runs.get(run_id)
    if run is None:
        print(f"Run {run_id} not found.", file=sys.stderr)
        return 1
    if run.status.is_terminal:
        print(f"Run {run_id} is already {run.status.value}.")
        return 0
    RunService(settings, store).stop(run_id, kill=kill)
    print(f"{'Kill' if kill else 'Stop'} requested for run {run_id}.")
    return 0


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
    notifications = store.notifications.list(run_id)
    if notifications:
        print("Notifications:")
    for item in notifications:
        error = f" {item.last_error}" if item.last_error else ""
        print(f"  {item.kind} via {item.channel}: {item.status.value}{error}")
    return 0


def _test_notifications(settings: Settings) -> int:
    config = settings.notifications
    variables = load_variables(resolve_environment_paths(settings.project_root))
    channels = build_channels(config.channels, variables)
    if not channels:
        print("No usable notification channel is configured.", file=sys.stderr)
        return 1
    if not config.enabled:
        print("Note: notifications.enabled is false, runs send nothing.")
    message = Message("Test message", f"dailyscraper on {socket.gethostname()} can reach you.")
    failed = 0
    for channel in channels:
        outcome = channel.send(message)
        if isinstance(outcome, Sent):
            print(f"{channel.name}: sent")
        else:
            failed += 1
            print(f"{channel.name}: {outcome.reason}")
    return 1 if failed else 0


def _headed(settings: Settings) -> Settings:
    playwright = settings.browser.playwright.model_copy(update={"headless": False})
    browser = settings.browser.model_copy(update={"playwright": playwright})
    return settings.model_copy(update={"browser": browser})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dailyscraper")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run scraper packages in parallel slots")
    run.add_argument("scrapers", nargs="+")
    run.add_argument(
        "--input", type=Path, help="CSV with detail rows instead of the links (one scraper)"
    )
    run.add_argument("--headed", action="store_true", help="show the browser windows")

    stop = commands.add_parser("stop", help="stop a running run")
    stop.add_argument("run_id")
    stop.add_argument("--kill", action="store_true", help="kill the slots at once")

    runs = commands.add_parser("runs", help="history of runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--project")
    runs_commands = runs.add_subparsers(dest="runs_command")
    show = runs_commands.add_parser("show", help="details of one run")
    show.add_argument("run_id")

    notifications = commands.add_parser("notifications", help="notification channels")
    notifications_commands = notifications.add_subparsers(dest="notifications_command")
    notifications_commands.required = True
    notifications_commands.add_parser("test", help="send a test message to every channel")
    return parser


if __name__ == "__main__":
    sys.exit(main())

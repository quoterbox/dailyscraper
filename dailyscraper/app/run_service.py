from __future__ import annotations

import json
import logging
import os
import socket
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil

from dailyscraper.browsers import DEFAULT_OPENER, BrowserManager, SlotConfig
from dailyscraper.dispatch import Dispatcher, DispatchResult, Gates
from dailyscraper.domain import (
    DIRECT_PROXY,
    Attempt,
    Command,
    CommandKind,
    DeliveryRecord,
    Dimension,
    EventLevel,
    Proxy,
    RunRecord,
    RunStatus,
    Task,
    TaskKind,
    TaskStatus,
    new_run_id,
)
from dailyscraper.observability import RunEvents, redact
from dailyscraper.observability.notifications import Notifier, build_notifier
from dailyscraper.output import (
    Deliverer,
    DeliveryContext,
    make_archive,
    scraper_records,
    scraper_rows,
    write_output,
)
from dailyscraper.proxies import (
    ProxyAllocator,
    ProxyHealth,
    ProxyPool,
    build_pool,
    check_proxies,
)
from dailyscraper.schema import Inputs, ScraperPackage, load_scraper_package
from dailyscraper.settings import Settings, load_variables
from dailyscraper.settings.environment import resolve_environment_paths
from dailyscraper.storage import RunLayout, Store
from dailyscraper.tasks.discovery import after_discovery
from dailyscraper.tasks.inputs import InputSnapshot, create_input_snapshot
from dailyscraper.tasks.planner import plan_tasks
from dailyscraper.tasks.retry import RetryPolicy

logger = logging.getLogger(__name__)

FINAL_ROW_STATUSES = frozenset({"ok", "not_found"})
INTERVENTION_EVENTS = (
    "intervention.required",
    "intervention.resolved",
    "intervention.timeout",
    "intervention.aborted",
)


@dataclass(frozen=True)
class RunRequest:
    scrapers: tuple[str, ...]
    input_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.scrapers:
            raise ValueError("a run needs at least one scraper")
        if self.input_path is not None and len(self.scrapers) != 1:
            raise ValueError("an input file can be given for one scraper only")


@dataclass(frozen=True)
class RunReport:
    run: RunRecord
    layout: RunLayout
    outputs: dict[str, list[Path]] = field(default_factory=dict)
    deliveries: list[DeliveryRecord] = field(default_factory=list)


@dataclass
class _Execution:
    status: RunStatus = RunStatus.FAILED
    error: str | None = None
    snapshots: dict[str, InputSnapshot] = field(default_factory=dict)
    dispatch: DispatchResult | None = None
    gates: Gates | None = None

    def fail(self, status: RunStatus, error: str) -> None:
        if self.status in {RunStatus.FAILED, RunStatus.INTERRUPTED} and self.error:
            return
        self.status = status
        self.error = error


class RunService:
    """Plans a run, executes it in slot processes and finalizes it whatever happens."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        opener: str = DEFAULT_OPENER,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.store = store
        self.opener = opener
        self._now = now
        self._sleep = sleep

    @property
    def host_id(self) -> str:
        return self.settings.runtime.host_id or socket.gethostname()

    def load_package(self, name: str) -> ScraperPackage:
        variables = load_variables(resolve_environment_paths(self.settings.project_root))
        return load_scraper_package(self.settings.paths.scrapers / name, variables=variables)

    def stop(self, run_id: str, *, kill: bool = False) -> Command:
        return self.store.commands.issue(run_id, CommandKind.KILL if kill else CommandKind.STOP)

    def reconcile_orphans(self) -> list[str]:
        """Close runs of this machine whose parent process is gone."""
        orphaned = []
        for run in self.store.runs.list(limit=200):
            if (
                run.status is RunStatus.RUNNING
                and run.host_id == self.host_id
                and not _process_alive(run.pid)
            ):
                self.store.runs.finish(
                    run.run_id,
                    RunStatus.INTERRUPTED,
                    error="the run's process ended without finishing it",
                )
                orphaned.append(run.run_id)
                logger.warning("Run %s was left unfinished and is closed now", run.run_id)
        return orphaned

    def run(self, request: RunRequest) -> RunReport:
        self.reconcile_orphans()
        packages = {name: self.load_package(name) for name in request.scrapers}
        started = self._now()
        run_id = new_run_id(started)
        layout = RunLayout.for_run(self.settings.paths.data, run_id)
        layout.root.mkdir(parents=True)
        config = redact(self.settings.model_dump(mode="json"))
        layout.config.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
        self.store.runs.create(
            RunRecord(
                run_id=run_id,
                project_id=self.settings.project_id,
                status=RunStatus.RUNNING,
                started_at=self.store.now(),
                scrapers=tuple(packages),
                packages={name: package.version for name, package in packages.items()},
                config=config,
                host_id=self.host_id,
                pid=os.getpid(),
            )
        )
        events = RunEvents(self.store, run_id=run_id, project_id=self.settings.project_id)
        notifier = self._notifier(run_id, events)
        try:
            events.emit("run.started", scrapers=list(packages), host=self.host_id)
            logger.info("Run %s started: %s", run_id, ", ".join(packages))

            execution = _Execution()
            try:
                self._execute(run_id, layout, packages, request, execution, events)
            except KeyboardInterrupt:
                execution.fail(RunStatus.INTERRUPTED, "interrupted")
                logger.warning("Run %s interrupted", run_id)
            except Exception as error:
                execution.fail(RunStatus.FAILED, f"{type(error).__name__}: {error}")
                logger.exception("Run %s failed", run_id)
            return self._finalize(run_id, layout, packages, started, execution, events)
        finally:
            if notifier is not None:
                notifier.stop()

    def _notifier(self, run_id: str, events: RunEvents) -> Notifier | None:
        try:
            variables = load_variables(resolve_environment_paths(self.settings.project_root))
            notifier = build_notifier(self.settings.notifications, self.store, run_id, variables)
        except Exception:
            logger.exception("Notifications are off for run %s", run_id)
            return None
        if notifier is not None:
            events.listeners.append(notifier.observe)
            notifier.start()
        return notifier

    def _execute(
        self,
        run_id: str,
        layout: RunLayout,
        packages: Mapping[str, ScraperPackage],
        request: RunRequest,
        execution: _Execution,
        events: RunEvents,
    ) -> None:
        runtime = self.settings.runtime
        if request.input_path is not None:
            (name,) = packages
            execution.snapshots[name] = create_input_snapshot(
                request.input_path,
                layout.inputs / f"{name}.csv",
                packages[name].definition.inputs or Inputs(),
            )
        specs = [
            spec
            for name, package in packages.items()
            for spec in plan_tasks(
                run_id,
                package,
                snapshot=execution.snapshots.get(name),
                items_per_task=runtime.items_per_task,
                pages_per_task=runtime.pages_per_task,
                max_attempts=runtime.task_attempts,
            )
        ]
        self.store.tasks.enqueue(specs)
        events.emit("run.planned", tasks=len(specs))
        gates = execution.gates = Gates(
            packages, probing={spec.scraper for spec in specs if spec.kind is TaskKind.PROBE}
        )

        sites = {name: site_key(package) for name, package in packages.items()}
        pool = build_pool(self.settings.proxies)
        health = ProxyHealth(self.store, self.settings.proxy_health)
        allocator = ProxyAllocator(pool, health)
        self._check_proxies(pool, packages, sites, health, events)
        closed = self._proxy_thresholds(pool, allocator, packages, sites, events)

        def slot_config(scraper: str, slot_id: str, proxy: Proxy | None) -> SlotConfig:
            return self._slot_config(
                run_id, layout, packages[scraper], slot_id, execution, sites[scraper], proxy
            )

        def proxies(scraper: str, avoid) -> list[Proxy | None]:
            return allocator.candidates(
                sites[scraper], self.settings.selected_proxies(scraper), avoid
            )

        dispatcher = Dispatcher(
            store=self.store,
            browsers=BrowserManager(
                store=self.store,
                run_id=run_id,
                host_id=self.host_id,
                runtime=runtime,
                configs=slot_config,
                rules=self.settings.limit_rules(),
                sessions=lambda scraper: {
                    Dimension.SITE: sites[scraper],
                    Dimension.PROJECT: self.settings.project_id,
                },
                proxies=proxies,
            ),
            events=events,
            run_id=run_id,
            host_id=self.host_id,
            scrapers=list(packages),
            retry=RetryPolicy(runtime.retry_delay_seconds, runtime.retry_max_delay_seconds),
            health=health,
            sites=sites,
            poll_interval=runtime.poll_interval_seconds,
            stop_grace=runtime.stop_grace_seconds,
            gates=gates,
            blocking={
                name: bool(
                    package.definition.health_check and package.definition.health_check.blocking
                )
                for name, package in packages.items()
            },
            followups=lambda task, outcome: after_discovery(
                task,
                outcome,
                packages[task.scraper],
                pages_per_task=runtime.pages_per_task,
                max_attempts=runtime.task_attempts,
                saved_pages=self._saved_pages(task, events),
            ),
        )
        for name, reason in closed.items():
            dispatcher.close_gate(name, reason)
        execution.dispatch = dispatcher.run()
        if execution.dispatch.failure is not None:
            execution.fail(RunStatus.FAILED, execution.dispatch.failure)
            return
        if execution.dispatch.stopped:
            execution.fail(RunStatus.INTERRUPTED, "; ".join(execution.dispatch.reasons))
            return
        execution.status = self._outcome(run_id, packages, execution)

    def _slot_config(
        self,
        run_id: str,
        layout: RunLayout,
        package: ScraperPackage,
        slot_id: str,
        execution: _Execution,
        site: str,
        proxy: Proxy | None,
    ) -> SlotConfig:
        definition = package.definition
        inputs = definition.inputs or Inputs()
        pages = definition.pages
        if pages.detail is not None:
            link_field = pages.detail.link_field
        elif pages.list is not None:
            link_field = pages.list.detail_link_field
        else:
            link_field = inputs.detail_link_field
        snapshot = execution.snapshots.get(package.name)
        return SlotConfig(
            slot_id=slot_id,
            run_id=run_id,
            run_root=layout.root,
            settings=self.settings.model_dump(mode="json"),
            package_root=package.root,
            package_version=package.version,
            definition=definition.model_dump(mode="json"),
            identity_fields=inputs.identity_fields,
            link_field=link_field,
            input_snapshot=snapshot.path if snapshot else None,
            opener=self.opener,
            proxy=proxy,
            **self._session_dirs(site, proxy),
        )

    def _session_dirs(self, site: str, proxy: Proxy | None) -> dict[str, Path | None]:
        if self.settings.browser.isolation != "persistent":
            return {"profile_dir": None, "extension_dir": None}
        proxy_id = proxy.proxy_id if proxy is not None else DIRECT_PROXY
        return {
            "profile_dir": self.settings.profiles_dir / site / proxy_id,
            "extension_dir": self.settings.extensions_dir / site / proxy_id if proxy else None,
        }

    def _check_proxies(
        self,
        pool: ProxyPool,
        packages: Mapping[str, ScraperPackage],
        sites: Mapping[str, str],
        health: ProxyHealth,
        events: RunEvents,
    ) -> None:
        """Open the circuit of proxies that fail a plain HTTP request, for every site of the run."""
        if pool.empty or not self.settings.proxy_check.enabled:
            return
        used = {
            proxy.proxy_id: proxy
            for name in packages
            for proxy in pool.select(self.settings.selected_proxies(name))
        }
        results = check_proxies(list(used.values()), self.settings.proxy_check)
        for result in results:
            events.emit(
                "proxy.checked",
                level=EventLevel.INFO if result.ok else EventLevel.WARNING,
                proxy=result.proxy_id,
                ok=result.ok,
                exit_ip=result.exit_ip,
                category=result.category,
                error=result.error,
                seconds=round(result.seconds, 2),
            )
            if not result.ok:
                for site in set(sites.values()):
                    health.open_now(site, used[result.proxy_id], result.category or "network")
        failed = sum(1 for result in results if not result.ok)
        logger.info("Network check: %s of %s proxies failed", failed, len(results))

    def _saved_pages(self, task: Task, events: RunEvents) -> list[int]:
        """Pages whose items the result keeps: those of the task's latest attempt with rows."""
        numbers = [
            int(commit.attempt_id.rpartition("#")[2])
            for commit in self.store.partitions.commits(task.run_id, task.scraper)
            if commit.task_id == task.task_id and commit.attempt_id
        ]
        if not numbers:
            return []
        attempt_id = f"{task.task_id}#{max(numbers)}"
        attempt = next(
            (
                a
                for a in self.store.tasks.attempts(task.run_id, task_id=task.task_id)
                if a.attempt_id == attempt_id
            ),
            None,
        )
        saved = attempt.details.get("saved_pages") if attempt is not None else None
        if saved is None:
            events.emit(
                "discovery.pages_unknown",
                level=EventLevel.WARNING,
                scraper=task.scraper,
                attempt=attempt_id,
                reason="the attempt that saved rows ended without a report; its pages may repeat",
            )
            return []
        return [int(page) for page in saved]

    def _proxy_thresholds(
        self,
        pool: ProxyPool,
        allocator: ProxyAllocator,
        packages: Mapping[str, ScraperPackage],
        sites: Mapping[str, str],
        events: RunEvents,
    ) -> dict[str, str]:
        """Scrapers to close for too few usable proxies; raises when the whole run has too few."""
        if pool.empty:
            return {}
        check = self.settings.proxy_check
        closed: dict[str, str] = {}
        selected_all: set[str] = set()
        usable_all: set[str] = set()
        for name in packages:
            names = self.settings.selected_proxies(name)
            selected = {proxy.proxy_id for proxy in pool.select(names)}
            usable = {
                proxy.proxy_id
                for proxy in allocator.candidates(sites[name], names)
                if proxy is not None
            }
            selected_all |= selected
            usable_all |= usable
            if not _enough(
                len(usable),
                len(selected),
                check.min_healthy_per_scraper,
                check.min_healthy_ratio_per_scraper,
            ):
                closed[name] = f"{len(usable)} of {len(selected)} proxies usable"
        events.emit(
            "proxy.thresholds",
            level=EventLevel.WARNING if closed else EventLevel.INFO,
            usable=len(usable_all),
            total=len(selected_all),
            closed=closed,
        )
        if not _enough(
            len(usable_all), len(selected_all), check.min_healthy_total, check.min_healthy_ratio
        ):
            raise RunBlocked(f"{len(usable_all)} of {len(selected_all)} proxies usable")
        return closed

    def _outcome(
        self, run_id: str, packages: Mapping[str, ScraperPackage], execution: _Execution
    ) -> RunStatus:
        # A probe that failed without closing its gate is a warning; the work decides.
        unfinished = {TaskStatus.FAILED, TaskStatus.CANCELLED}
        if any(
            task.status in unfinished and task.kind is not TaskKind.PROBE
            for task in self.store.tasks.list(run_id)
        ):
            return RunStatus.COMPLETED_WITH_ERRORS
        for name in packages:
            snapshot = execution.snapshots.get(name)
            if snapshot is None:
                continue
            statuses = self.store.partitions.item_statuses(run_id, name)
            if len(statuses) < snapshot.rows or set(statuses.values()) - FINAL_ROW_STATUSES:
                return RunStatus.COMPLETED_WITH_ERRORS
        for attempt in _final_attempts(self.store.tasks.attempts(run_id)).values():
            if attempt.details.get("failures"):
                return RunStatus.COMPLETED_WITH_ERRORS
        return RunStatus.COMPLETED

    def _finalize(
        self,
        run_id: str,
        layout: RunLayout,
        packages: Mapping[str, ScraperPackage],
        started: datetime,
        execution: _Execution,
        events: RunEvents,
    ) -> RunReport:
        outputs: dict[str, list[Path]] = {}
        rows: dict[str, int] = {}
        try:
            outputs, rows = self._export(run_id, layout, packages, execution.snapshots)
        except Exception as error:
            execution.fail(RunStatus.FAILED, f"export failed: {error}")
            logger.exception("Run %s: export failed", run_id)

        deliveries = Deliverer(
            base_dir=self.settings.project_root,
            clock=self.store.now,
            record=self.store.deliveries.record,
            sleep=self._sleep,
        ).run(
            self.settings.output.deliveries,
            outputs,
            DeliveryContext(
                run_id=run_id,
                project_id=self.settings.project_id,
                date=f"{started:%Y-%m-%d}",
                succeeded=execution.status.is_completed,
                work_dir=layout.output,
            ),
        )
        for delivery in deliveries:
            events.emit(
                "delivery.finished",
                level=EventLevel.INFO if delivery.error is None else EventLevel.WARNING,
                name=delivery.name,
                status=delivery.status.value,
                location=delivery.location,
                error=delivery.error,
            )

        summary = self._summary(
            run_id, layout, packages, started, execution, outputs, rows, deliveries
        )
        layout.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
        run = self.store.runs.finish(
            run_id, execution.status, summary=summary, error=execution.error
        )
        events.emit(
            "run.finished",
            level=EventLevel.INFO if execution.status.is_completed else EventLevel.ERROR,
            status=execution.status.value,
            rows=sum(rows.values()),
            error=execution.error,
            scrapers={name: rows.get(name, 0) for name in packages},
            duration_seconds=summary["duration_seconds"],
            output=str(layout.output),
        )
        logger.info(
            "Run %s finished: %s, %s rows", run_id, execution.status.value, sum(rows.values())
        )
        return RunReport(run=run, layout=layout, outputs=outputs, deliveries=deliveries)

    def _export(
        self,
        run_id: str,
        layout: RunLayout,
        packages: Mapping[str, ScraperPackage],
        snapshots: Mapping[str, InputSnapshot],
    ) -> tuple[dict[str, list[Path]], dict[str, int]]:
        output = self.settings.output
        formats = dict.fromkeys(output.formats)
        for delivery in output.deliveries:
            formats.update(dict.fromkeys(delivery.formats or ()))
        outputs: dict[str, list[Path]] = {fmt: [] for fmt in formats}
        rows: dict[str, int] = {}
        for name in packages:
            snapshot = snapshots.get(name)
            for fmt in formats:
                path = layout.output / f"{name}.{fmt}"
                rows[name] = write_output(
                    fmt,
                    lambda name=name, snapshot=snapshot: scraper_rows(
                        self.store.partitions, layout, run_id, name, snapshot
                    ),
                    path,
                )
                outputs[fmt].append(path)
        if output.archive:
            files = [path for paths in outputs.values() for path in paths]
            if files:
                outputs["zip"] = [make_archive(files, layout.output / f"{run_id}.zip")]
        return outputs, rows

    def _summary(
        self,
        run_id: str,
        layout: RunLayout,
        packages: Mapping[str, ScraperPackage],
        started: datetime,
        execution: _Execution,
        outputs: dict[str, list[Path]],
        rows: dict[str, int],
        deliveries: list[DeliveryRecord],
    ) -> dict[str, Any]:
        finished = self._now()
        tasks = self.store.tasks.list(run_id)
        attempts = self.store.tasks.attempts(run_id)
        final = _final_attempts(attempts)
        gates = execution.gates.summary() if execution.gates is not None else {}
        interventions = Counter(
            (event.scraper, event.type.removeprefix("intervention."))
            for event in self.store.events.list(run_id, types=INTERVENTION_EVENTS)
        )
        scrapers: dict[str, Any] = {}
        for name, package in packages.items():
            snapshot = execution.snapshots.get(name)
            own = [task for task in tasks if task.scraper == name]
            scrapers[name] = {
                "version": package.version,
                "rows": rows.get(name, 0),
                "statuses": self._statuses(run_id, layout, name, snapshot),
                "tasks": dict(Counter(task.status.value for task in own)),
                "attempts": sum(task.attempts for task in own),
                "gate": gates.get(name),
                "discovery": [
                    {
                        key: final[task.task_id].details.get(key)
                        for key in ("link", "status", "last_page", "saved_pages", "reason")
                    }
                    for task in own
                    if task.kind is TaskKind.DISCOVER and task.task_id in final
                ],
                "interventions": {
                    result: count
                    for (scraper, result), count in sorted(interventions.items())
                    if scraper == name
                },
                "proxies": dict(
                    Counter(
                        attempt.proxy_id or DIRECT_PROXY
                        for attempt in attempts
                        if attempt.task_id in {task.task_id for task in own}
                    )
                ),
                "input": None
                if snapshot is None
                else {
                    "rows": snapshot.rows,
                    "skipped_invalid": snapshot.skipped_invalid,
                    "skipped_duplicates": snapshot.skipped_duplicates,
                },
                "links": [
                    dict(final[task.task_id].details)
                    for task in own
                    if task.kind is TaskKind.LIST and task.task_id in final
                ],
                "outputs": {
                    fmt: [layout.relative(path) for path in paths if path.stem == name]
                    for fmt, paths in outputs.items()
                    if fmt != "zip"
                },
            }
        return {
            "run_id": run_id,
            "project_id": self.settings.project_id,
            "host_id": self.host_id,
            "status": execution.status.value,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_seconds": round((finished - started).total_seconds(), 1),
            "error": execution.error,
            "slots": len({attempt.slot_id for attempt in attempts}),
            "scrapers": scrapers,
            "archive": [layout.relative(path) for path in outputs.get("zip", [])],
            "deliveries": [
                {
                    "name": delivery.name,
                    "status": delivery.status.value,
                    "location": delivery.location,
                    "error": delivery.error,
                }
                for delivery in deliveries
            ],
        }

    def _statuses(
        self, run_id: str, layout: RunLayout, name: str, snapshot: InputSnapshot | None
    ) -> dict[str, int]:
        if snapshot is not None:
            return dict(Counter(self.store.partitions.item_statuses(run_id, name).values()))
        records = scraper_records(self.store.partitions, layout, run_id, name)
        return dict(Counter(record.get("status") or "ok" for record in records))


class RunBlocked(RuntimeError):
    """The run cannot start its work: too few usable proxies."""


def _enough(usable: int, total: int, minimum: int, ratio: float) -> bool:
    return usable >= minimum and usable >= ratio * total


def site_key(package: ScraperPackage) -> str:
    """The site a package works on, shared by every package with the same domain."""
    host = (urlsplit(package.definition.website).hostname or package.name).lower()
    return host.removeprefix("www.")


def _final_attempts(attempts: list[Attempt]) -> dict[str, Attempt]:
    final: dict[str, Attempt] = {}
    for attempt in attempts:
        current = final.get(attempt.task_id)
        if current is None or attempt.attempt_no > current.attempt_no:
            final[attempt.task_id] = attempt
    return final


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        return psutil.Process(pid).is_running()
    except psutil.Error:
        return False

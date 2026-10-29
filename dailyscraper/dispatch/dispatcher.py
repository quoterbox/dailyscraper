from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dailyscraper.browsers.manager import BrowserManager, SlotEvent, SlotExited, SlotMessage
from dailyscraper.browsers.messages import (
    Ack,
    InterventionEnded,
    InterventionStarted,
    ItemsCommitted,
    PartitionSealed,
    TaskFinished,
    commit_ref,
    seal_ref,
)
from dailyscraper.dispatch.gates import Gates
from dailyscraper.domain import (
    AttemptStatus,
    CommandKind,
    ErrorCategory,
    EventLevel,
    Proxy,
    Task,
    TaskKind,
    TaskOutcome,
    TaskStatus,
)
from dailyscraper.observability import RunEvents
from dailyscraper.proxies import ProxyHealth
from dailyscraper.storage import Store
from dailyscraper.tasks.discovery import FollowUp
from dailyscraper.tasks.retry import RetryPolicy, Verdict

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    stopped: bool = False
    killed: bool = False
    interrupted: bool = False
    reasons: list[str] = field(default_factory=list)
    failure: str | None = None


def proxy_signal(outcome: TaskOutcome) -> str | None:
    """The failure that speaks about the proxy, or ``None`` when the session went fine."""
    if outcome.status is AttemptStatus.FAILED and outcome.category is not None:
        return outcome.category.value
    for failure in outcome.failures:
        category = failure.get("category")
        if category and category != ErrorCategory.NOT_FOUND:
            return str(category)
    return None


class Dispatcher:
    """The single owner of task order, slot assignment and retries for one run."""

    def __init__(
        self,
        *,
        store: Store,
        browsers: BrowserManager,
        events: RunEvents,
        run_id: str,
        host_id: str,
        scrapers: Sequence[str],
        retry: RetryPolicy,
        health: ProxyHealth | None = None,
        sites: Mapping[str, str] | None = None,
        poll_interval: float = 0.5,
        stop_grace: float = 30.0,
        gates: Gates | None = None,
        blocking: Mapping[str, bool] | None = None,
        followups: Callable[[Task, TaskOutcome], FollowUp] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.browsers = browsers
        self.events = events
        self.run_id = run_id
        self.host_id = host_id
        self.scrapers = list(scrapers)
        self.retry = retry
        self.health = health
        self.sites = dict(sites or {})
        self.poll_interval = poll_interval
        self.stop_grace = stop_grace
        self.gates = gates or Gates(self.scrapers)
        self.blocking = dict(blocking or {})
        self.followups = followups
        self._clock = clock
        self.result = DispatchResult()
        self._stop_requested_at: float | None = None
        self._cancel_sent = False
        self._sessions: dict[str, tuple[str, Proxy | None]] = {}

    @property
    def stopping(self) -> bool:
        return self._stop_requested_at is not None

    def run(self) -> DispatchResult:
        try:
            while not self._finished():
                try:
                    self._step()
                except KeyboardInterrupt:
                    if self.stopping:
                        self._kill_all("interrupted twice")
                    else:
                        self.result.interrupted = True
                        self.stop("interrupted")
        finally:
            self._drain()
        return self.result

    def stop(self, reason: str) -> None:
        if self.stopping:
            return
        logger.warning("Run %s stopping: %s", self.run_id, reason)
        self.result.stopped = True
        self.result.reasons.append(reason)
        self._stop_requested_at = self._clock()
        self.store.tasks.cancel_pending(self.run_id)
        self.events.emit("run.stopping", level=EventLevel.WARNING, reason=reason)

    def _step(self) -> None:
        self._apply_commands()
        if not self.stopping:
            self._fill()
        for event in self.browsers.poll(self.poll_interval):
            self._handle(event)
        if self.stopping:
            self._escalate()

    def _finished(self) -> bool:
        counts = self.store.tasks.counts(self.run_id)
        running = counts.get(TaskStatus.RUNNING, 0)
        if self.stopping:
            return running == 0
        return running == 0 and counts.get(TaskStatus.PENDING, 0) == 0

    def _fill(self) -> None:
        while True:
            allowed = [
                scraper
                for scraper in self.scrapers
                if self.browsers.idle_slot(scraper) or self.browsers.can_start(scraper)
            ]
            task = None
            for scrapers, kinds in self.gates.claimable(allowed):
                task = self.store.tasks.claim(self.run_id, scrapers=scrapers, kinds=kinds)
                if task is not None:
                    break
            if task is None:
                return
            slot = self.browsers.idle_slot(task.scraper, task.avoid) or self.browsers.start(
                task.scraper, task.avoid
            )
            if slot is None:
                self.store.tasks.release(task.task_id)
                return
            attempt = self.store.tasks.start(
                task.task_id, slot_id=slot.slot_id, host_id=self.host_id, proxy_id=slot.proxy_id
            )
            self._sessions[attempt.attempt_id] = (slot.slot_id, slot.proxy)
            self.browsers.submit(slot, task, attempt)
            logger.info("Task %s started in %s", attempt.attempt_id, slot.slot_id)

    def _handle(self, event: SlotEvent) -> None:
        if isinstance(event, SlotExited):
            if event.attempt_id is not None:
                outcome = TaskOutcome(
                    AttemptStatus.CANCELLED if self.result.killed else AttemptStatus.FAILED,
                    category=event.category,
                    error=event.reason,
                )
                self._close_attempt(event.attempt_id, outcome)
            return
        message = event.message
        if isinstance(message, ItemsCommitted):
            self._acknowledge(
                event, commit_ref(message.commit), self.store.partitions.commit, message.commit
            )
        elif isinstance(message, PartitionSealed):
            self._acknowledge(
                event, seal_ref(message.seal), self.store.partitions.seal, message.seal
            )
        elif isinstance(message, TaskFinished):
            self._close_attempt(message.attempt_id, message.outcome)
        elif isinstance(message, InterventionStarted | InterventionEnded):
            self._intervention(event, message)

    def _intervention(
        self, event: SlotMessage, message: InterventionStarted | InterventionEnded
    ) -> None:
        slot = self.browsers.slots.get(event.slot_id)
        common = {
            "scraper": event.scraper,
            "slot": event.slot_id,
            "attempt": message.attempt_id,
            "proxy": slot.proxy_id if slot is not None else None,
            "host": self.host_id,
            "kind": message.kind,
            "url": message.url,
        }
        if isinstance(message, InterventionStarted):
            deadline = datetime.fromisoformat(self.store.now()) + timedelta(
                seconds=message.timeout_seconds
            )
            self.events.emit(
                "intervention.required",
                level=EventLevel.WARNING,
                page_type=message.page_type,
                reason=message.reason,
                timeout_seconds=message.timeout_seconds,
                deadline_at=deadline.isoformat(timespec="seconds"),
                **common,
            )
            return
        self.events.emit(
            f"intervention.{message.result}",
            level=EventLevel.INFO if message.result == "resolved" else EventLevel.WARNING,
            seconds=message.seconds,
            **common,
        )

    def _acknowledge(self, event: SlotMessage, ref: str, register, value) -> None:
        try:
            register(value)
        except Exception as error:
            logger.warning("Rejected %s from %s: %s", ref, event.slot_id, error)
            self.browsers.send(event.slot_id, Ack(ref=ref, ok=False, error=str(error)))
            return
        self.browsers.send(event.slot_id, Ack(ref=ref, ok=True))

    def _close_attempt(self, attempt_id: str, outcome: TaskOutcome) -> None:
        task = self.store.tasks.get(attempt_id.rpartition("#")[0])
        if task is None or task.status is not TaskStatus.RUNNING:
            return
        for failure in outcome.failures:
            level = (
                EventLevel.INFO if failure.get("category") == "not_found" else EventLevel.WARNING
            )
            self.events.emit("item.failed", level=level, scraper=task.scraper, **failure)
        failed_proxy = self._report_proxy(task.scraper, attempt_id, outcome)
        done_rows = None
        if task.kind is TaskKind.DETAIL:
            done_rows = self.store.partitions.item_statuses(
                self.run_id, task.scraper, attempt_id=attempt_id
            )
        decision = self.retry.decide(task, outcome, done_rows)
        if decision.verdict is Verdict.RETRY and not self.stopping:
            due = datetime.fromisoformat(self.store.now()) + timedelta(
                seconds=decision.delay_seconds
            )
            self.store.tasks.reschedule(
                attempt_id,
                outcome,
                not_before=due.isoformat(timespec="milliseconds"),
                payload=decision.payload,
                avoid=[failed_proxy] if failed_proxy else (),
            )
            verdict = "retry"
        else:
            if (
                self.stopping
                and outcome.status is AttemptStatus.FAILED
                and decision.verdict is Verdict.RETRY
            ):
                outcome = TaskOutcome(
                    AttemptStatus.CANCELLED,
                    category=ErrorCategory.CANCELLED,
                    rows=outcome.rows,
                    error=outcome.error,
                    details=outcome.details,
                )
            self.store.tasks.finish(attempt_id, outcome)
            verdict = outcome.status.value
            if not self.stopping:
                self._finished_task(task, outcome)
        self.events.emit(
            "task.finished",
            level=EventLevel.INFO
            if outcome.status is AttemptStatus.COMPLETED
            else EventLevel.WARNING,
            scraper=task.scraper,
            attempt=attempt_id,
            verdict=verdict,
            category=outcome.category.value if outcome.category else None,
            rows=outcome.rows,
            error=outcome.error,
        )

    def _finished_task(self, task: Task, outcome: TaskOutcome) -> None:
        if task.kind is TaskKind.PROBE:
            self._probe_finished(task, outcome)
        elif task.kind is TaskKind.DISCOVER and self.followups is not None:
            self._discovery_finished(task, outcome)

    def _probe_finished(self, task: Task, outcome: TaskOutcome) -> None:
        scraper = task.scraper
        probe = dict(outcome.details or {})
        if outcome.status is AttemptStatus.COMPLETED:
            self.gates.open(scraper, "health probe passed")
            self.events.emit(
                "gate.opened", scraper=scraper, reason="health probe passed", probe=probe
            )
            return
        reason = f"health probe failed: {outcome.error or outcome.category}"
        if self.blocking.get(scraper):
            self.close_gate(scraper, reason, probe=probe)
            return
        self.gates.open(scraper, reason)
        self.events.emit(
            "gate.opened", level=EventLevel.WARNING, scraper=scraper, reason=reason, probe=probe
        )

    def close_gate(self, scraper: str, reason: str, **data) -> None:
        """The scraper takes no more tasks in this run; its pending ones are cancelled."""
        self.gates.close(scraper, reason)
        cancelled = self.store.tasks.cancel_pending(self.run_id, scraper=scraper)
        logger.error("Scraper %s closed: %s", scraper, reason)
        self.events.emit(
            "gate.closed",
            level=EventLevel.ERROR,
            scraper=scraper,
            reason=reason,
            cancelled=cancelled,
            **data,
        )

    def _discovery_finished(self, task: Task, outcome: TaskOutcome) -> None:
        followup = self.followups(task, outcome)
        if followup.specs:
            self.store.tasks.enqueue(followup.specs)
        details = outcome.details or {}
        self.events.emit(
            "discovery.finished",
            level=EventLevel.INFO if followup.error is None else EventLevel.ERROR,
            scraper=task.scraper,
            link=task.payload.get("link"),
            status=details.get("status"),
            last_page=followup.last_page,
            reason=details.get("reason") or outcome.error,
            tasks=len(followup.specs),
        )
        if followup.error is not None:
            self.result.failure = followup.error
            self.stop(followup.error)

    def _report_proxy(self, scraper: str, attempt_id: str, outcome: TaskOutcome) -> str | None:
        """Tell proxy health how the session did; returns the proxy to avoid on retry."""
        slot_id, proxy = self._sessions.pop(attempt_id, (None, None))
        if self.health is None or proxy is None or outcome.status is AttemptStatus.CANCELLED:
            return None
        category = proxy_signal(outcome)
        site = self.sites.get(scraper, scraper)
        transition = self.health.report(site, proxy, category)
        if transition.after.status != transition.before.status or transition.opened:
            self.events.emit(
                "proxy.health",
                level=EventLevel.WARNING if transition.opened else EventLevel.INFO,
                scraper=scraper,
                site=site,
                proxy=proxy.proxy_id,
                status=transition.after.status.value,
                category=category,
                generation=transition.after.generation,
                cooldown_until=transition.after.cooldown_until,
            )
        if transition.opened and slot_id is not None:
            # The session keeps its proxy for life: a burned or rotated proxy needs a new slot.
            self.browsers.retire(slot_id)
        policy = self.health.policy
        if category in policy.immediate_open_categories or category in policy.tracked_categories:
            return proxy.proxy_id
        return None

    def _apply_commands(self) -> None:
        for command in self.store.commands.pending(self.run_id):
            self.store.commands.mark_applied(command.command_id)
            if command.kind is CommandKind.KILL:
                self.stop("kill command")
                self._kill_all("kill command")
            else:
                self.stop("stop command")

    def _escalate(self) -> None:
        elapsed = self._clock() - (self._stop_requested_at or self._clock())
        if not self._cancel_sent and elapsed >= self.stop_grace:
            self._cancel_sent = True
            for slot in self.browsers.alive():
                if slot.attempt_id is not None:
                    self.browsers.cancel(slot.slot_id)
        if elapsed >= 2 * self.stop_grace:
            self._kill_all("stop grace exceeded")

    def _kill_all(self, reason: str) -> None:
        self.result.killed = True
        for slot in self.browsers.alive():
            exited = self.browsers.kill(slot.slot_id, reason, ErrorCategory.CANCELLED)
            if exited is not None:
                self._handle(exited)

    def _drain(self) -> None:
        """Let idle slots close their partitions, then kill whatever does not exit in time."""
        for slot in self.browsers.alive():
            self.browsers.retire(slot.slot_id)
        deadline = self._clock() + self.stop_grace
        while self.browsers.alive() and self._clock() < deadline:
            try:
                for event in self.browsers.poll(self.poll_interval):
                    self._handle(event)
            except KeyboardInterrupt:
                break
        for slot in self.browsers.alive():
            exited = self.browsers.kill(slot.slot_id, "did not exit", ErrorCategory.CANCELLED)
            if exited is not None:
                self._handle(exited)

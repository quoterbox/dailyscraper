from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dailyscraper.browsers.manager import BrowserManager, SlotEvent, SlotExited, SlotMessage
from dailyscraper.browsers.messages import (
    Ack,
    ItemsCommitted,
    PartitionSealed,
    TaskFinished,
    commit_ref,
    seal_ref,
)
from dailyscraper.domain import (
    AttemptStatus,
    CommandKind,
    ErrorCategory,
    EventLevel,
    TaskKind,
    TaskOutcome,
    TaskStatus,
)
from dailyscraper.observability import RunEvents
from dailyscraper.storage import Store
from dailyscraper.tasks.retry import RetryPolicy, Verdict

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    stopped: bool = False
    killed: bool = False
    interrupted: bool = False
    reasons: list[str] = field(default_factory=list)


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
        max_parallel: Mapping[str, int],
        retry: RetryPolicy,
        poll_interval: float = 0.5,
        stop_grace: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.browsers = browsers
        self.events = events
        self.run_id = run_id
        self.host_id = host_id
        self.max_parallel = dict(max_parallel)
        self.retry = retry
        self.poll_interval = poll_interval
        self.stop_grace = stop_grace
        self._clock = clock
        self.result = DispatchResult()
        self._stop_requested_at: float | None = None
        self._cancel_sent = False

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
                for scraper, limit in self.max_parallel.items()
                if self.browsers.idle_slot(scraper) or self.browsers.can_start(scraper, limit)
            ]
            task = self.store.tasks.claim(self.run_id, scrapers=allowed) if allowed else None
            if task is None:
                return
            slot = self.browsers.idle_slot(task.scraper) or self.browsers.start(task.scraper)
            if slot is None:
                self.store.tasks.release(task.task_id)
                return
            attempt = self.store.tasks.start(
                task.task_id, slot_id=slot.slot_id, host_id=self.host_id
            )
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

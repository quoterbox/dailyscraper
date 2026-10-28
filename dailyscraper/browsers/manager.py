from __future__ import annotations

import logging
import multiprocessing
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from multiprocessing.connection import Connection, wait
from typing import Any

import psutil

from dailyscraper.browsers.messages import (
    Cancel,
    Message,
    RunTask,
    SessionBroken,
    Shutdown,
    SlotFailed,
    SlotReady,
    TaskFinished,
    decode_from_slot,
    encode,
)
from dailyscraper.browsers.slot import SlotConfig, slot_main
from dailyscraper.browsers.termination import terminate_tree
from dailyscraper.domain import (
    DIRECT_PROXY,
    Attempt,
    Dimension,
    ErrorCategory,
    LeaseKey,
    LimitRule,
    Proxy,
    Task,
    lease_keys,
)
from dailyscraper.settings.models import RuntimeSettings
from dailyscraper.storage import Store

logger = logging.getLogger(__name__)

BROWSER_KIND = "browser"
MB = 1024 * 1024


class SlotState(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"


@dataclass
class SlotHandle:
    slot_id: str
    scraper: str
    process: Any
    connection: Connection
    lease_holder: str
    started_at: float
    state: SlotState = SlotState.STARTING
    attempt_id: str | None = None
    task_started_at: float = 0.0
    last_message_at: float = 0.0
    failure: str | None = None
    proxy: Proxy | None = None
    tasks_done: int = 0

    @property
    def proxy_id(self) -> str:
        return self.proxy.proxy_id if self.proxy is not None else DIRECT_PROXY


@dataclass(frozen=True)
class SlotMessage:
    slot_id: str
    scraper: str
    message: Message


@dataclass(frozen=True)
class SlotExited:
    slot_id: str
    scraper: str
    attempt_id: str | None
    category: ErrorCategory
    reason: str


SlotEvent = SlotMessage | SlotExited


@dataclass
class BrowserManager:
    """Slot processes of one run on this machine.

    A slot starts only when it gets a place in every limit that applies to its
    session (``rules`` over host, site, proxy, scraper… shared by all runs and
    machines on the store) and the machine has the memory budget. The watchdog
    kills a slot that stops reporting or overruns the task deadline; only that
    slot is affected.
    """

    store: Store
    run_id: str
    host_id: str
    runtime: RuntimeSettings
    configs: Callable[[str, str, Proxy | None], SlotConfig]
    rules: Sequence[LimitRule] = ()
    sessions: Callable[[str], Mapping[Dimension, str]] = lambda scraper: {}
    proxies: Callable[[str, Collection[str]], list[Proxy | None]] = lambda scraper, avoid: [None]
    clock: Callable[[], float] = time.monotonic
    available_memory_mb: Callable[[], float] = lambda: psutil.virtual_memory().available / MB
    terminate: Callable[[int], object] = terminate_tree
    slots: dict[str, SlotHandle] = field(default_factory=dict)
    _numbers: dict[str, int] = field(default_factory=dict)
    _leases_renewed_at: float = 0.0

    def slots_of(self, scraper: str) -> list[SlotHandle]:
        return [slot for slot in self.slots.values() if slot.scraper == scraper]

    def idle_slot(self, scraper: str, avoid: Collection[str] = ()) -> SlotHandle | None:
        return next(
            (
                slot
                for slot in self.slots_of(scraper)
                if slot.state is SlotState.IDLE and slot.proxy_id not in avoid
            ),
            None,
        )

    def lease_keys(self, scraper: str, proxy: Proxy | None = None) -> list[LeaseKey]:
        session = {
            Dimension.HOST: self.host_id,
            Dimension.RUN: self.run_id,
            Dimension.KIND: BROWSER_KIND,
            Dimension.PROXY: proxy.proxy_id if proxy is not None else DIRECT_PROXY,
            Dimension.SCRAPER: scraper,
            **self.sessions(scraper),
        }
        return lease_keys(self.rules, session)

    def can_start(self, scraper: str) -> bool:
        if not any(
            not self.store.leases.blocked(self.lease_keys(scraper, proxy))
            for proxy in self.proxies(scraper, ())
        ):
            return False
        if not self.slots:
            return True
        free = self.available_memory_mb() - self.runtime.memory_reserve_mb
        if free < self.runtime.slot_memory_mb:
            logger.info("Not starting a slot for %s: %.0f MB free", scraper, free)
            return False
        return True

    def start(self, scraper: str, avoid: Collection[str] = ()) -> SlotHandle | None:
        """A new slot on the best proxy that fits every limit; ``None`` if none does."""
        number = self._numbers.get(scraper, 0) + 1
        slot_id = f"{scraper}-{number}"
        holder = f"{self.run_id}/{slot_id}"
        proxy = self._lease_proxy(scraper, holder, avoid)
        if proxy is False:
            return None
        self._numbers[scraper] = number
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        config = self.configs(scraper, slot_id, proxy)
        process = context.Process(
            target=slot_main,
            args=(config.model_dump_json(), child),
            name=f"slot-{slot_id}",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            self.store.leases.release(holder)
            raise
        child.close()
        now = self.clock()
        slot = SlotHandle(
            slot_id=slot_id,
            scraper=scraper,
            process=process,
            connection=parent,
            lease_holder=holder,
            started_at=now,
            last_message_at=now,
            proxy=proxy,
        )
        self.slots[slot_id] = slot
        logger.info("Slot %s started (pid %s) via %s", slot_id, process.pid, slot.proxy_id)
        return slot

    def _lease_proxy(
        self, scraper: str, holder: str, avoid: Collection[str]
    ) -> Proxy | None | bool:
        full: list[str] = []
        for proxy in self.proxies(scraper, avoid):
            full = self.store.leases.acquire(
                holder,
                self.lease_keys(scraper, proxy),
                run_id=self.run_id,
                ttl_seconds=self.runtime.lease_ttl_seconds,
            )
            if not full:
                return proxy
        logger.info("Not starting a slot for %s, limits reached: %s", scraper, ", ".join(full))
        return False

    def submit(self, slot: SlotHandle, task: Task, attempt: Attempt) -> None:
        self.send(
            slot.slot_id,
            RunTask(
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                kind=task.kind,
                payload=dict(task.payload),
            ),
        )
        now = self.clock()
        slot.state = SlotState.BUSY
        slot.attempt_id = attempt.attempt_id
        slot.task_started_at = now
        slot.last_message_at = now

    def send(self, slot_id: str, message: Message) -> None:
        slot = self.slots.get(slot_id)
        if slot is None:
            return
        try:
            slot.connection.send_bytes(encode(message))
        except (OSError, ValueError) as error:
            logger.warning("Cannot send to slot %s: %s", slot_id, error)

    def cancel(self, slot_id: str) -> None:
        self.send(slot_id, Cancel())

    def retire(self, slot_id: str) -> None:
        slot = self.slots.get(slot_id)
        if slot is not None and slot.state is not SlotState.STOPPING:
            self.send(slot_id, Shutdown())
            slot.state = SlotState.STOPPING

    def poll(self, timeout: float) -> list[SlotEvent]:
        self._renew_leases()
        events: list[SlotEvent] = []
        if self.slots:
            waitables = [slot.connection for slot in self.slots.values()]
            waitables += [slot.process.sentinel for slot in self.slots.values()]
            ready = set(wait(waitables, timeout))
        else:
            time.sleep(timeout)
            ready = set()
        for slot in list(self.slots.values()):
            if slot.connection in ready or slot.process.sentinel in ready:
                events.extend(self._read(slot))
            if not slot.process.is_alive():
                events.extend(self._read(slot))
                events.append(self._exited(slot, self._exit_reason(slot)))
        events.extend(self._watchdog())
        return events

    def kill(self, slot_id: str, reason: str, category: ErrorCategory) -> SlotExited | None:
        slot = self.slots.get(slot_id)
        if slot is None:
            return None
        logger.warning("Killing slot %s: %s", slot_id, reason)
        if slot.process.pid:
            self.terminate(slot.process.pid)
        slot.process.join(timeout=5)
        return self._exited(slot, reason, category)

    def alive(self) -> list[SlotHandle]:
        return list(self.slots.values())

    def _read(self, slot: SlotHandle) -> list[SlotEvent]:
        events: list[SlotEvent] = []
        try:
            while slot.connection.poll(0):
                message = decode_from_slot(slot.connection.recv_bytes())
                slot.last_message_at = self.clock()
                self._track(slot, message)
                events.append(SlotMessage(slot.slot_id, slot.scraper, message))
        except (EOFError, OSError):
            pass
        return events

    def _track(self, slot: SlotHandle, message: Message) -> None:
        if isinstance(message, SlotReady) and slot.state is SlotState.STARTING:
            slot.state = SlotState.IDLE
        elif isinstance(message, TaskFinished) and message.attempt_id == slot.attempt_id:
            slot.attempt_id = None
            slot.tasks_done += 1
            if slot.state is SlotState.BUSY:
                slot.state = SlotState.IDLE
            if self._worn_out(slot):
                self.retire(slot.slot_id)
        elif isinstance(message, SlotFailed):
            slot.failure = message.error
        elif isinstance(message, SessionBroken):
            slot.failure = message.reason

    def _worn_out(self, slot: SlotHandle) -> bool:
        runtime = self.runtime
        if runtime.slot_max_tasks and slot.tasks_done >= runtime.slot_max_tasks:
            return True
        age = self.clock() - slot.started_at
        return bool(runtime.slot_max_age_seconds and age >= runtime.slot_max_age_seconds)

    def _exit_reason(self, slot: SlotHandle) -> str:
        code = slot.process.exitcode
        return slot.failure or f"slot process exited with code {code}"

    def _exited(
        self, slot: SlotHandle, reason: str, category: ErrorCategory | None = None
    ) -> SlotExited:
        self.slots.pop(slot.slot_id, None)
        try:
            slot.connection.close()
        except OSError:
            pass
        try:
            self.store.leases.release(slot.lease_holder)
        except Exception:
            logger.warning("Cannot release lease of %s", slot.slot_id, exc_info=True)
        if category is None:
            category = ErrorCategory.BROWSER_CLOSED
            if slot.failure and slot.state is SlotState.STARTING:
                category = ErrorCategory.INTERNAL
        logger.info("Slot %s exited: %s", slot.slot_id, reason)
        return SlotExited(slot.slot_id, slot.scraper, slot.attempt_id, category, reason)

    def _watchdog(self) -> list[SlotEvent]:
        now = self.clock()
        runtime = self.runtime
        events: list[SlotEvent] = []
        for slot in list(self.slots.values()):
            if slot.state is SlotState.STARTING and slot.attempt_id is None:
                overdue = now - slot.started_at > runtime.slot_start_timeout_seconds
                reason = "slot did not start in time"
            elif slot.state is SlotState.BUSY:
                stalled = now - slot.last_message_at > runtime.stall_timeout_seconds
                late = now - slot.task_started_at > runtime.task_deadline_seconds
                overdue = stalled or late
                reason = "no progress from the slot" if stalled else "task deadline exceeded"
            else:
                overdue = False
                reason = ""
            if overdue:
                exited = self.kill(slot.slot_id, reason, ErrorCategory.TIMEOUT)
                if exited is not None:
                    events.append(exited)
        return events

    def _renew_leases(self) -> None:
        now = self.clock()
        if now - self._leases_renewed_at < self.runtime.lease_ttl_seconds / 3:
            return
        self._leases_renewed_at = now
        holders = [slot.lease_holder for slot in self.slots.values()]
        try:
            self.store.leases.renew(holders, ttl_seconds=self.runtime.lease_ttl_seconds)
        except Exception:
            logger.warning("Cannot renew slot leases", exc_info=True)

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from dailyscraper.detection.classifier import (
    PageProbe,
    classify_page,
    is_hard_terminal,
    ready_is_sufficient,
)
from dailyscraper.detection.policy import StatePolicy
from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.transport.errors import is_browser_closed_error

logger = logging.getLogger(__name__)

UNSAFE_FOR_ACTIVITY = {
    PageState.CAPTCHA,
    PageState.BLOCKED,
    PageState.PROXY_AUTH,
    PageState.LOAD_FAILED,
    PageState.BLANK,
    PageState.EMPTY,
    PageState.NOT_FOUND,
}


class Reload(Protocol):
    @property
    def ok(self) -> bool: ...

    @property
    def error(self) -> str | None: ...


class PageControl(Protocol):
    def wait(self, milliseconds: float) -> None: ...

    def reload(self) -> Reload: ...

    def forget_navigation_status(self) -> None: ...


ClassifyHook = Callable[[PageType, PageOutcome], PageOutcome]
Intervention = Callable[[PageOutcome, Callable[[], PageOutcome]], PageOutcome]


@dataclass
class StateWaiter:
    control: PageControl
    make_probe: Callable[[bool], PageProbe]
    hook: ClassifyHook | None = None
    check_interrupted: Callable[[], None] = lambda: None
    activity: Callable[[], None] | None = None
    intervention: Intervention | None = None

    def wait_for(self, policy: StatePolicy, *, allow_reload: bool = True) -> PageOutcome:
        outcome = self._probe_and_intervene(policy)
        if is_hard_terminal(outcome.state):
            return outcome

        if policy.activity_after_open and self._run_activity(outcome):
            outcome = self._probe_and_intervene(policy)
            if is_hard_terminal(outcome.state):
                return outcome

        outcome = self._intervene(self._wait_for_change(outcome, policy), policy)
        if self._settled(outcome, policy):
            return outcome
        if not allow_reload or outcome.state not in policy.reload_on:
            return self._late_empty(outcome, policy)

        for attempt in range(1, policy.reload_attempts + 1):
            self.check_interrupted()
            logger.warning(
                "Reloading page: type=%s state=%s attempt=%s/%s url=%s",
                policy.page_type.value,
                outcome.state.value,
                attempt,
                policy.reload_attempts,
                outcome.url,
            )
            navigation = self.control.reload()
            if not navigation.ok:
                return replace(
                    outcome, state=PageState.LOAD_FAILED, reason=f"reload_failed:{navigation.error}"
                )
            self._pause(policy.reload_wait_ms)
            outcome = self._probe_and_intervene(policy)
            if is_hard_terminal(outcome.state):
                return outcome
            if policy.activity_after_reload:
                self._run_activity(outcome)
            outcome = self._intervene(self._wait_for_change(outcome, policy), policy)
            if self._settled(outcome, policy):
                return outcome

        return self._late_empty(outcome, policy)

    def probe(self, policy: StatePolicy, *, ignore_status: bool = False) -> PageOutcome:
        if policy.settle_seconds > 0:
            self._pause(policy.settle_seconds * 1000)
        outcome = classify_page(self.make_probe(ignore_status), policy)
        if self.hook is None:
            return outcome
        try:
            replacement = self.hook(policy.page_type, outcome)
        except Exception as error:
            if is_browser_closed_error(error):
                raise
            logger.warning("Page state hook failed, keeping %s: %s", outcome.state.value, error)
            return outcome
        return replacement if isinstance(replacement, PageOutcome) else outcome

    def _probe_and_intervene(self, policy: StatePolicy) -> PageOutcome:
        return self._intervene(self.probe(policy), policy)

    def _intervene(self, outcome: PageOutcome, policy: StatePolicy) -> PageOutcome:
        if self.intervention is None or outcome.state is not PageState.CAPTCHA:
            return outcome
        # A solved in-page challenge keeps the original (usually 403) response
        # status, so the live DOM decides and the stale status is dropped.
        resolved = self.intervention(outcome, lambda: self.probe(policy, ignore_status=True))
        if resolved.state is PageState.READY:
            self.control.forget_navigation_status()
        return resolved

    def _wait_for_change(self, outcome: PageOutcome, policy: StatePolicy) -> PageOutcome:
        for _ in range(policy.wait_attempts):
            if self._settled(outcome, policy):
                return outcome
            self._pause(policy.wait_interval_ms)
            outcome = self.probe(policy)
        return outcome

    def _settled(self, outcome: PageOutcome, policy: StatePolicy) -> bool:
        return ready_is_sufficient(outcome, policy) or is_hard_terminal(outcome.state)

    def _late_empty(self, outcome: PageOutcome, policy: StatePolicy) -> PageOutcome:
        # Recommendation blocks may sit next to valid or still loading results,
        # so these selectors only count once waiting for readiness has failed.
        if (
            policy.page_type is not PageType.LIST
            or outcome.state is not PageState.UNKNOWN
            or not policy.signals.empty_after_wait
        ):
            return outcome
        probe = self.make_probe(False)
        if any(probe.is_visible(selector) for selector in policy.signals.empty_after_wait):
            return replace(outcome, state=PageState.EMPTY, reason="empty_after_wait")
        return outcome

    def _run_activity(self, outcome: PageOutcome) -> bool:
        if self.activity is None or outcome.state in UNSAFE_FOR_ACTIVITY:
            return False
        self.check_interrupted()
        self.activity()
        return True

    def _pause(self, milliseconds: float) -> None:
        self.check_interrupted()
        self.control.wait(milliseconds)
        self.check_interrupted()

"""Waiting for a person to solve a challenge in the open browser window."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.execution.timing import Pacer
from dailyscraper.settings.models import ManualInterventionSettings

logger = logging.getLogger(__name__)

TIMEOUT_REASON = "manual_intervention_timeout"
# A page that is still redirecting after the challenge can look empty for a moment.
CONCLUSIVE_AFTER_REPEAT = frozenset({PageState.EMPTY, PageState.NOT_FOUND})


InterventionResult = Literal["resolved", "timeout", "aborted"]


@dataclass(frozen=True)
class InterventionNotice:
    kind: str
    page_type: PageType
    url: str | None
    reason: str | None
    timeout_seconds: float


class InterventionReporter(Protocol):
    def started(self, notice: InterventionNotice) -> None: ...

    def ended(
        self, notice: InterventionNotice, *, result: InterventionResult, seconds: float
    ) -> None: ...


class ManualIntervention:
    """The ``StateWaiter`` intervention: keep the page open and re-check it until solved."""

    def __init__(
        self,
        settings: ManualInterventionSettings,
        *,
        headless: bool,
        pacer: Pacer,
        reporter: InterventionReporter | None = None,
        bring_to_front: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.headless = headless
        self.pacer = pacer
        self.reporter = reporter
        self.bring_to_front = bring_to_front
        self._clock = clock
        self._headless_warned = False

    def allows(self, outcome: PageOutcome) -> bool:
        settings = self.settings
        if not settings.enabled or outcome.state is not PageState.CAPTCHA:
            return False
        if outcome.page_type.value not in settings.page_types:
            return False
        if self.headless and not settings.allow_headless:
            if not self._headless_warned:
                self._headless_warned = True
                logger.warning(
                    "CAPTCHA in a headless browser: manual solving needs a visible browser"
                    " (--headed) or interventions.manual.allow_headless"
                )
            return False
        return True

    def __call__(self, outcome: PageOutcome, probe: Callable[[], PageOutcome]) -> PageOutcome:
        if not self.allows(outcome):
            return outcome
        notice = InterventionNotice(
            kind="captcha",
            page_type=outcome.page_type,
            url=outcome.url,
            reason=outcome.reason,
            timeout_seconds=self.settings.timeout_seconds,
        )
        logger.warning(
            "\nMANUAL INTERVENTION REQUIRED\n"
            "  Type     CAPTCHA\n"
            "  Page     %s\n"
            "  Timeout  %s min\n"
            "  Action   solve the CAPTCHA in the open browser window\n"
            "  URL      %s",
            notice.page_type.value,
            round(notice.timeout_seconds / 60, 1),
            notice.url or "-",
        )
        started = self._clock()
        result: PageOutcome | None = None
        ended: InterventionResult = "aborted"
        if self.reporter is not None:
            self.reporter.started(notice)
        try:
            self._front()
            result = self._wait(probe, started + notice.timeout_seconds)
            ended = "timeout" if result is None else "resolved"
        finally:
            seconds = round(self._clock() - started, 1)
            if self.reporter is not None:
                self.reporter.ended(notice, result=ended, seconds=seconds)
        if result is not None:
            logger.info("CAPTCHA solved in %ss: %s", seconds, result.url)
            return result
        logger.warning("CAPTCHA was not solved in %ss: %s", seconds, notice.url)
        return replace(outcome, reason=f"{TIMEOUT_REASON}:{outcome.reason or 'captcha'}")

    def _wait(self, probe: Callable[[], PageOutcome], deadline: float) -> PageOutcome | None:
        previous: PageState | None = None
        while (remaining := deadline - self._clock()) > 0:
            self.pacer.pause(min(self.settings.poll_interval_seconds, remaining))
            result = probe()
            if result.state is PageState.READY:
                return result
            if result.state in CONCLUSIVE_AFTER_REPEAT and result.state is previous:
                return result
            previous = result.state
        return None

    def _front(self) -> None:
        if self.bring_to_front is None or self.headless:
            return
        try:
            self.bring_to_front()
        except Exception as error:
            logger.debug("Cannot bring the page to front: %s", error)

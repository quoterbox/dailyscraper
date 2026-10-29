from __future__ import annotations

import pytest

from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.execution.interventions import TIMEOUT_REASON, ManualIntervention
from dailyscraper.settings.models import ManualInterventionSettings


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakePacer:
    def __init__(self, clock: Clock, *, cancel_after: int | None = None) -> None:
        self.clock = clock
        self.pauses: list[float] = []
        self.cancel_after = cancel_after

    def pause(self, seconds: float) -> None:
        if self.cancel_after is not None and len(self.pauses) >= self.cancel_after:
            raise KeyboardInterrupt
        self.pauses.append(seconds)
        self.clock.now += seconds


class Reporter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def started(self, notice) -> None:
        self.calls.append(("started", notice.kind, notice.page_type, notice.url))

    def ended(self, notice, *, result, seconds) -> None:
        self.calls.append(("ended", result, seconds))


def captcha(page_type: PageType = PageType.LIST) -> PageOutcome:
    return PageOutcome(PageState.CAPTCHA, page_type, "https://shop.test/c", reason="marker")


def probes(*states: PageState):
    sequence = iter(states)

    def probe() -> PageOutcome:
        state = next(sequence, states[-1])
        return PageOutcome(state, PageType.LIST, "https://shop.test/c", reason=state.value)

    return probe


def intervention(clock: Clock, pacer=None, *, headless: bool = False, **settings):
    options = {"timeout_seconds": 10, "poll_interval_seconds": 2, **settings}
    reporter = Reporter()
    manual = ManualIntervention(
        ManualInterventionSettings(**options),
        headless=headless,
        pacer=pacer or FakePacer(clock),
        reporter=reporter,
        clock=clock,
    )
    return manual, reporter


def test_waits_on_the_open_page_until_a_person_solves_it() -> None:
    clock = Clock()
    manual, reporter = intervention(clock)

    result = manual(captcha(), probes(PageState.CAPTCHA, PageState.CAPTCHA, PageState.READY))

    assert result.state is PageState.READY
    assert reporter.calls == [
        ("started", "captcha", PageType.LIST, "https://shop.test/c"),
        ("ended", "resolved", 6.0),
    ]


def test_gives_up_after_the_timeout_and_keeps_the_captcha_outcome() -> None:
    clock = Clock()
    pacer = FakePacer(clock)
    manual, reporter = intervention(clock, pacer, timeout_seconds=5)

    result = manual(captcha(), probes(PageState.CAPTCHA))

    assert result.state is PageState.CAPTCHA
    assert result.reason == f"{TIMEOUT_REASON}:marker"
    assert pacer.pauses == [2, 2, 1]
    assert reporter.calls[-1] == ("ended", "timeout", 5.0)


def test_empty_counts_as_solved_only_when_it_repeats() -> None:
    clock = Clock()
    manual, _ = intervention(clock)

    result = manual(
        captcha(), probes(PageState.EMPTY, PageState.CAPTCHA, PageState.EMPTY, PageState.EMPTY)
    )

    assert result.state is PageState.EMPTY
    assert clock.now == 8


def test_an_interrupted_wait_is_reported_as_aborted() -> None:
    clock = Clock()
    manual, reporter = intervention(clock, FakePacer(clock, cancel_after=1))

    with pytest.raises(KeyboardInterrupt):
        manual(captcha(), probes(PageState.CAPTCHA))

    assert reporter.calls[-1] == ("ended", "aborted", 2.0)


@pytest.mark.parametrize(
    ("outcome", "settings", "headless"),
    [
        (captcha(), {"enabled": False}, False),
        (captcha(), {}, True),
        (captcha(PageType.AUTH), {}, False),
        (PageOutcome(PageState.BLOCKED, PageType.LIST, "https://shop.test/c", "403"), {}, False),
    ],
)
def test_passes_through_what_it_must_not_wait_for(outcome, settings, headless) -> None:
    clock = Clock()
    manual, reporter = intervention(clock, headless=headless, **settings)

    assert manual(outcome, probes(PageState.READY)) is outcome
    assert reporter.calls == []


def test_allow_headless_waits_in_a_headless_browser() -> None:
    clock = Clock()
    manual, _ = intervention(clock, headless=True, allow_headless=True)

    assert manual(captcha(), probes(PageState.READY)).state is PageState.READY

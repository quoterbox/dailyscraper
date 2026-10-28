from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from dailyscraper.detection import (
    StateWaiter,
    build_policy,
    category_for_state,
    classify_error,
    classify_page,
    state_for_navigation_error,
)
from dailyscraper.domain import ErrorCategory, PageOutcome, PageState, PageType
from dailyscraper.pages import PageStateRules, Selector

ITEMS = Selector.model_validate({"css": "li.product"})
READY = Selector.model_validate({"css": "h1.title"})
STOP = Selector.model_validate({"css": ".no-products"})


@dataclass
class FakeProbe:
    body: str = "Catalog of products"
    url: str = "https://shop.test/catalog/"
    http_status: int | None = 200
    page_title: str = "Shop"
    visible: set[Selector] = field(default_factory=set)
    counts: dict[Selector, int] = field(default_factory=dict)
    ld_types: set[str] = field(default_factory=set)

    def title(self) -> str:
        return self.page_title

    def body_text(self) -> str:
        return self.body

    def is_visible(self, selector: Selector) -> bool:
        return selector in self.visible

    def count(self, selector: Selector) -> int:
        return self.counts.get(selector, 0)

    def json_ld_types(self) -> set[str]:
        return self.ld_types


def list_policy(**rules):
    return build_policy(
        PageStateRules.model_validate(rules), PageType.LIST, items=(ITEMS,), empty=(STOP,)
    )


def detail_policy(**rules):
    return build_policy(PageStateRules.model_validate(rules), PageType.DETAIL, ready=(READY,))


@pytest.mark.parametrize(
    ("probe", "state", "reason"),
    [
        (FakeProbe(http_status=407), PageState.PROXY_AUTH, "main_response_status:407"),
        (FakeProbe(body="ERR_PROXY_AUTH failed"), PageState.PROXY_AUTH, "proxy_auth_marker"),
        (FakeProbe(body="Please solve the captcha"), PageState.CAPTCHA, "captcha_marker"),
        (FakeProbe(http_status=429), PageState.BLOCKED, "main_response_status:429"),
        (FakeProbe(http_status=404), PageState.EMPTY, "main_response_status:404"),
        (FakeProbe(http_status=503), PageState.LOAD_FAILED, "main_response_status:503"),
        (FakeProbe(body="Access denied for you"), PageState.BLOCKED, "blocked_marker"),
        (FakeProbe(body="net::ERR_CONNECTION_RESET"), PageState.LOAD_FAILED, "browser_error"),
        (FakeProbe(body="   "), PageState.BLANK, "blank_page"),
        (FakeProbe(visible={STOP}, counts={ITEMS: 5}), PageState.EMPTY, "empty_selector"),
        (FakeProbe(body="Ничего не найдено"), PageState.EMPTY, "empty_marker"),
        (FakeProbe(counts={ITEMS: 9}), PageState.READY, "items_found"),
        (FakeProbe(), PageState.UNKNOWN, "no_items_and_no_empty_signal"),
    ],
)
def test_list_page_states_follow_the_check_order(probe, state, reason) -> None:
    outcome = classify_page(probe, list_policy())

    assert outcome.state is state
    assert outcome.reason.startswith(reason)


def test_ready_list_reports_the_item_count() -> None:
    assert classify_page(FakeProbe(counts={ITEMS: 9}), list_policy()).items_count == 9


def test_support_email_mentioning_captcha_is_not_a_challenge() -> None:
    probe = FakeProbe(body="Blocked. Write to captcha-support@shop.test", counts={ITEMS: 3})

    assert classify_page(probe, list_policy()).state is PageState.READY


def test_package_markers_extend_the_defaults() -> None:
    probe = FakeProbe(body="incident: fab_cp_123", counts={ITEMS: 3})

    assert classify_page(probe, list_policy()).state is PageState.READY
    assert (
        classify_page(probe, list_policy(blocked_text_markers=["fab_cp"])).state
        is PageState.BLOCKED
    )


@pytest.mark.parametrize(
    ("probe", "state"),
    [
        (FakeProbe(http_status=404), PageState.NOT_FOUND),
        (FakeProbe(visible={READY}), PageState.READY),
        (FakeProbe(ld_types={"product"}), PageState.READY),
        (FakeProbe(body="Товар не найден"), PageState.NOT_FOUND),
        (FakeProbe(body="Товар не найден", visible={READY}), PageState.READY),
        (FakeProbe(), PageState.UNKNOWN),
    ],
)
def test_detail_page_states(probe, state) -> None:
    assert classify_page(probe, detail_policy()).state is state


@dataclass
class FakeControl:
    waits: list[float] = field(default_factory=list)
    reloads: int = 0
    reload_error: str | None = None
    forgot_status: bool = False

    def wait(self, milliseconds: float) -> None:
        self.waits.append(milliseconds)

    def reload(self):
        self.reloads += 1
        return type("Nav", (), {"ok": self.reload_error is None, "error": self.reload_error})()

    def forget_navigation_status(self) -> None:
        self.forgot_status = True


def waiter(probes: list[FakeProbe], control: FakeControl, **kwargs) -> StateWaiter:
    sequence = iter(probes)
    last: list[FakeProbe] = []

    def make_probe(ignore_status: bool) -> FakeProbe:
        last[:] = [next(sequence, last[0] if last else probes[-1])]
        return last[0]

    return StateWaiter(control=control, make_probe=make_probe, **kwargs)


def test_waiter_polls_until_items_appear() -> None:
    control = FakeControl()
    probes = [FakeProbe(), FakeProbe(), FakeProbe(counts={ITEMS: 4})]

    outcome = waiter(probes, control).wait_for(list_policy(wait_interval_ms=10))

    assert outcome.state is PageState.READY
    assert control.waits == [10, 10]
    assert control.reloads == 0


def test_waiter_reloads_an_unknown_page_when_allowed() -> None:
    control = FakeControl()
    policy = list_policy(wait_attempts=1, wait_interval_ms=0, reload_attempts=1, reload_wait_ms=0)
    probes = [FakeProbe(), FakeProbe(), FakeProbe(counts={ITEMS: 2})]

    outcome = waiter(probes, control).wait_for(policy)

    assert outcome.state is PageState.READY
    assert control.reloads == 1


def test_waiter_stops_immediately_on_hard_terminal_states() -> None:
    control = FakeControl()

    outcome = waiter([FakeProbe(http_status=403)], control).wait_for(list_policy())

    assert outcome.state is PageState.BLOCKED
    assert control.waits == []


def test_waiter_reports_failed_reload() -> None:
    control = FakeControl(reload_error="net::ERR_CONNECTION_RESET")
    policy = list_policy(wait_attempts=0, reload_attempts=1)

    outcome = waiter([FakeProbe()], control).wait_for(policy)

    assert outcome.state is PageState.LOAD_FAILED
    assert outcome.reason.startswith("reload_failed")


def test_late_empty_selector_applies_only_after_waiting() -> None:
    late = Selector.model_validate({"css": ".recommendations"})
    policy = list_policy(
        wait_attempts=1, wait_interval_ms=0, empty_after_wait_selectors=[late.model_dump()]
    )

    outcome = waiter([FakeProbe(visible={late})], FakeControl()).wait_for(policy)

    assert outcome.state is PageState.EMPTY
    assert outcome.reason == "empty_after_wait"


def test_package_hook_can_reclassify_and_hook_errors_are_ignored() -> None:
    def to_blocked(page_type: PageType, outcome: PageOutcome) -> PageOutcome:
        return PageOutcome(PageState.BLOCKED, page_type, outcome.url, "hook")

    def broken(page_type: PageType, outcome: PageOutcome) -> PageOutcome:
        raise RuntimeError("bug in package hook")

    ready = [FakeProbe(counts={ITEMS: 1})]
    assert (
        waiter(ready, FakeControl(), hook=to_blocked).wait_for(list_policy()).state
        is PageState.BLOCKED
    )
    assert (
        waiter(ready, FakeControl(), hook=broken).wait_for(list_policy()).state is PageState.READY
    )


def test_solved_captcha_drops_the_stale_navigation_status() -> None:
    control = FakeControl()

    def solve(outcome: PageOutcome, reprobe) -> PageOutcome:
        return reprobe()

    probes = [FakeProbe(body="captcha"), FakeProbe(counts={ITEMS: 3})]

    outcome = waiter(probes, control, intervention=solve).wait_for(list_policy())

    assert outcome.state is PageState.READY
    assert control.forgot_status is True


@pytest.mark.parametrize(
    ("error", "category"),
    [
        ("Target page, context or browser has been closed", ErrorCategory.BROWSER_CLOSED),
        ("Tunnel connection failed: 407 Proxy Authentication Required", ErrorCategory.PROXY_AUTH),
        (
            'Timeout 30000ms exceeded navigating to "https://shop.test/p/ring-1407523/"',
            ErrorCategory.TIMEOUT,
        ),
        ("net::ERR_CONNECTION_RESET", ErrorCategory.NETWORK),
        (PermissionError(13, "denied", "out.csv"), ErrorCategory.FILESYSTEM),
        (ConnectionResetError(104, "reset"), ErrorCategory.NETWORK),
        ("something unexpected", ErrorCategory.INTERNAL),
    ],
)
def test_errors_map_to_one_category_set(error, category) -> None:
    assert classify_error(error) is category


def test_navigation_errors_and_states_map_to_categories() -> None:
    assert state_for_navigation_error("net::ERR_INVALID_AUTH_CREDENTIALS") is PageState.PROXY_AUTH
    assert state_for_navigation_error("Timeout 30000ms exceeded") is PageState.LOAD_FAILED
    assert category_for_state(PageState.BLOCKED, 429) is ErrorCategory.RATE_LIMITED
    assert category_for_state(PageState.READY) is None
    assert category_for_state(PageState.EMPTY) is None

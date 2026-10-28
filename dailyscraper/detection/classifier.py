from __future__ import annotations

from types import MappingProxyType
from typing import Protocol

from dailyscraper.detection.markers import (
    find_captcha_marker,
    find_marker,
)
from dailyscraper.detection.policy import StatePolicy
from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.pages import Selector

BLANK_URLS = {"", "about:blank", "chrome://newtab/", "chrome://new-tab-page/"}
BLOCKED_STATUSES = {401, 403, 429}
GONE_STATUSES = {404, 410}


class PageProbe(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def http_status(self) -> int | None: ...

    def title(self) -> str: ...

    def body_text(self) -> str: ...

    def is_visible(self, selector: Selector) -> bool: ...

    def count(self, selector: Selector) -> int: ...

    def json_ld_types(self) -> set[str]: ...


def classify_page(probe: PageProbe, policy: StatePolicy) -> PageOutcome:
    url = probe.url
    status = probe.http_status
    body = probe.body_text()
    title = probe.title()
    page_text = f"{title}\n{body}".lower()
    searchable = f"{url}\n{page_text}".lower()

    def outcome(
        state: PageState, reason: str, items_count: int | None = None, **evidence: str
    ) -> PageOutcome:
        return PageOutcome(
            state=state,
            page_type=policy.page_type,
            url=url,
            reason=reason,
            http_status=status,
            items_count=items_count,
            evidence=MappingProxyType(evidence),
        )

    if status == 407:
        return outcome(PageState.PROXY_AUTH, "main_response_status:407")
    if marker := find_marker(searchable, policy.proxy_auth_markers):
        return outcome(PageState.PROXY_AUTH, f"proxy_auth_marker:{marker}", marker=marker)
    if marker := find_captcha_marker(page_text, policy.captcha_markers):
        return outcome(PageState.CAPTCHA, f"captcha_marker:{marker}", marker=marker)
    if status in BLOCKED_STATUSES:
        return outcome(PageState.BLOCKED, f"main_response_status:{status}")
    if status in GONE_STATUSES:
        state = PageState.NOT_FOUND if policy.page_type is PageType.DETAIL else PageState.EMPTY
        return outcome(state, f"main_response_status:{status}")
    if status is not None and status >= 500:
        return outcome(PageState.LOAD_FAILED, f"main_response_status:{status}")
    if marker := find_marker(searchable, policy.blocked_markers):
        return outcome(PageState.BLOCKED, f"blocked_marker:{marker}", marker=marker)
    if marker := find_marker(searchable, policy.browser_error_markers):
        return outcome(PageState.LOAD_FAILED, f"browser_error_marker:{marker}", marker=marker)
    if _is_blank(url, body):
        return outcome(PageState.BLANK, "blank_page")

    if policy.page_type is PageType.LIST:
        return _classify_list(probe, policy, body.lower(), outcome)
    if policy.page_type is PageType.DETAIL:
        return _classify_detail(probe, policy, body.lower(), outcome)
    return _classify_generic(probe, policy, body, outcome)


def is_hard_terminal(state: PageState) -> bool:
    return state in {
        PageState.EMPTY,
        PageState.NOT_FOUND,
        PageState.CAPTCHA,
        PageState.BLOCKED,
        PageState.PROXY_AUTH,
    }


def _classify_list(probe: PageProbe, policy: StatePolicy, body: str, outcome) -> PageOutcome:
    signals = policy.signals
    if selector := _first_visible(probe, signals.empty):
        return outcome(PageState.EMPTY, "empty_selector", selector=selector)
    if marker := find_marker(body, policy.empty_markers):
        return outcome(PageState.EMPTY, f"empty_marker:{marker}", marker=marker)
    for selector in signals.items:
        count = probe.count(selector)
        if count > 0:
            return outcome(PageState.READY, "items_found", count, selector=_label(selector))
    if _is_loading(probe, policy, body):
        return outcome(PageState.LOADING, "loading_signal")
    return outcome(PageState.UNKNOWN, "no_items_and_no_empty_signal")


def _classify_detail(probe: PageProbe, policy: StatePolicy, body: str, outcome) -> PageOutcome:
    signals = policy.signals
    if selector := _first_visible(probe, signals.not_found):
        return outcome(PageState.NOT_FOUND, "not_found_selector", selector=selector)

    json_ld = next(iter(sorted(policy.json_ld_types & probe.json_ld_types())), None)
    matched = [_label(selector) for selector in signals.ready if probe.is_visible(selector)]
    selectors_ready = bool(matched) and (not policy.ready_all or len(matched) == len(signals.ready))
    if json_ld or selectors_ready:
        reason = f"json_ld_type:{json_ld}" if json_ld else f"ready_selector:{matched[0]}"
        return outcome(
            PageState.READY,
            reason,
            ready_selectors_matched=str(len(matched)),
            ready_selectors_total=str(len(signals.ready)),
        )
    # A product page can mention "out of stock" and still have useful content,
    # so textual not-found markers only count when no ready signal was seen.
    if marker := find_marker(body, policy.not_found_markers):
        return outcome(PageState.NOT_FOUND, f"not_found_marker:{marker}", marker=marker)
    if _is_loading(probe, policy, body):
        return outcome(PageState.LOADING, "loading_signal")
    return outcome(PageState.UNKNOWN, "no_ready_signal")


def _classify_generic(probe: PageProbe, policy: StatePolicy, body: str, outcome) -> PageOutcome:
    if selector := _first_visible(probe, policy.signals.ready):
        return outcome(PageState.READY, "ready_selector", selector=selector)
    if _is_loading(probe, policy, body.lower()):
        return outcome(PageState.LOADING, "loading_signal")
    if policy.body_is_ready and body.strip():
        return outcome(PageState.READY, "non_empty_body")
    return outcome(PageState.UNKNOWN, "no_ready_signal")


def ready_is_sufficient(outcome: PageOutcome, policy: StatePolicy) -> bool:
    if outcome.state is not PageState.READY:
        return False
    if policy.page_type is not PageType.DETAIL or not policy.prefer_ready_selectors:
        return True
    total = len(policy.signals.ready)
    if total == 0:
        return True
    matched = int(outcome.evidence.get("ready_selectors_matched", "0"))
    return matched == total if policy.ready_all else matched > 0


def _is_blank(url: str, body: str) -> bool:
    normalized = url.strip().lower()
    return normalized in BLANK_URLS or normalized.startswith("chrome-error://") or not body.strip()


def _is_loading(probe: PageProbe, policy: StatePolicy, body: str) -> bool:
    return bool(
        _first_visible(probe, policy.signals.loading) or find_marker(body, policy.loading_markers)
    )


def _first_visible(probe: PageProbe, selectors: tuple[Selector, ...]) -> str | None:
    return next((_label(selector) for selector in selectors if probe.is_visible(selector)), None)


def _label(selector: Selector) -> str:
    return " | ".join(f"{loc.kind.value}={loc.value}" for loc in selector.locators)

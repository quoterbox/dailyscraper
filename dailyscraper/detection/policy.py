from __future__ import annotations

from dataclasses import dataclass

from dailyscraper.detection import markers
from dailyscraper.domain import PageState, PageType
from dailyscraper.pages import PageStateRules, Selector

DEFAULT_RELOAD_ON = frozenset(
    {PageState.BLANK, PageState.LOADING, PageState.UNKNOWN, PageState.LOAD_FAILED}
)


@dataclass(frozen=True)
class Signals:
    ready: tuple[Selector, ...] = ()
    items: tuple[Selector, ...] = ()
    empty: tuple[Selector, ...] = ()
    empty_after_wait: tuple[Selector, ...] = ()
    not_found: tuple[Selector, ...] = ()
    loading: tuple[Selector, ...] = ()


@dataclass(frozen=True)
class StatePolicy:
    page_type: PageType
    wait_attempts: int
    wait_interval_ms: int
    reload_attempts: int
    reload_wait_ms: int
    reload_on: frozenset[PageState]
    settle_seconds: float
    activity_after_open: bool
    activity_after_reload: bool
    body_is_ready: bool
    ready_all: bool
    prefer_ready_selectors: bool
    json_ld_types: frozenset[str]
    empty_markers: tuple[str, ...]
    not_found_markers: tuple[str, ...]
    captcha_markers: tuple[str, ...]
    blocked_markers: tuple[str, ...]
    proxy_auth_markers: tuple[str, ...]
    browser_error_markers: tuple[str, ...]
    loading_markers: tuple[str, ...]
    signals: Signals


def build_policy(
    rules: PageStateRules,
    page_type: PageType,
    *,
    ready: tuple[Selector, ...] = (),
    items: tuple[Selector, ...] = (),
    empty: tuple[Selector, ...] = (),
) -> StatePolicy:
    def pick[T](value: T | None, default: T) -> T:
        return default if value is None else value

    is_detail = page_type is PageType.DETAIL
    return StatePolicy(
        page_type=page_type,
        wait_attempts=pick(rules.wait_attempts, 3),
        wait_interval_ms=pick(rules.wait_interval_ms, 1_000),
        reload_attempts=pick(rules.reload_attempts, 1 if is_detail else 0),
        reload_wait_ms=pick(rules.reload_wait_ms, 3_000),
        reload_on=frozenset(rules.reload_on) if rules.reload_on is not None else DEFAULT_RELOAD_ON,
        settle_seconds=pick(rules.settle_seconds, 0.0),
        activity_after_open=pick(rules.activity_after_open, True),
        activity_after_reload=pick(rules.activity_after_reload, False),
        body_is_ready=pick(rules.body_is_ready, True),
        ready_all=rules.ready_mode == "all",
        prefer_ready_selectors=pick(rules.prefer_ready_selectors, False),
        json_ld_types=frozenset(
            item.strip().lower()
            for item in pick(rules.json_ld_types, ("Product",) if is_detail else ())
        ),
        empty_markers=markers.merge_markers(markers.EMPTY_TEXT_MARKERS, rules.empty_text_markers),
        not_found_markers=markers.merge_markers(
            markers.NOT_FOUND_TEXT_MARKERS, rules.not_found_text_markers
        ),
        captcha_markers=markers.merge_markers(
            markers.CAPTCHA_TEXT_MARKERS, rules.captcha_text_markers
        ),
        blocked_markers=markers.merge_markers(
            markers.BLOCKED_TEXT_MARKERS, rules.blocked_text_markers
        ),
        proxy_auth_markers=markers.merge_markers(
            markers.PROXY_AUTH_TEXT_MARKERS, rules.proxy_auth_text_markers
        ),
        browser_error_markers=markers.merge_markers(
            markers.BROWSER_ERROR_TEXT_MARKERS, rules.browser_error_text_markers
        ),
        loading_markers=markers.merge_markers((), rules.loading_text_markers),
        signals=Signals(
            ready=_unique((*(rules.ready_selectors or ()), *ready)),
            items=_unique(items),
            empty=_unique((*(rules.empty_selectors or ()), *empty)),
            empty_after_wait=tuple(rules.empty_after_wait_selectors or ()),
            not_found=tuple(rules.not_found_selectors or ()),
            loading=tuple(rules.loading_selectors or ()),
        ),
    )


def _unique(selectors: tuple[Selector, ...]) -> tuple[Selector, ...]:
    result: list[Selector] = []
    for selector in selectors:
        if selector not in result:
            result.append(selector)
    return tuple(result)

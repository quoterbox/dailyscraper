"""Finding a catalog's last page: doubling steps to the first empty page, then bisection."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.execution.navigator import Navigator
from dailyscraper.execution.timing import Pacer
from dailyscraper.schema.scraper import Discovery

logger = logging.getLogger(__name__)


class PageKind(StrEnum):
    ITEMS = "items"
    EMPTY = "empty"
    BLOCKED = "blocked"
    PROXY_AUTH = "proxy_auth"
    FAILED = "failed"
    UNKNOWN = "unknown"


KINDS = {
    PageState.READY: PageKind.ITEMS,
    PageState.EMPTY: PageKind.EMPTY,
    # Many shops answer 404 past the last page.
    PageState.NOT_FOUND: PageKind.EMPTY,
    PageState.CAPTCHA: PageKind.BLOCKED,
    PageState.BLOCKED: PageKind.BLOCKED,
    PageState.PROXY_AUTH: PageKind.PROXY_AUTH,
    PageState.LOAD_FAILED: PageKind.FAILED,
    PageState.BLANK: PageKind.FAILED,
}


@dataclass(frozen=True)
class PageCheck:
    page: int
    kind: PageKind
    state: PageState
    reason: str


@dataclass(frozen=True)
class SearchResult:
    """``status``: ok, empty, or the kind of the page that stopped the search."""

    status: str
    last_page: int | None
    reason: str
    checks: tuple[PageCheck, ...] = ()
    saved_pages: tuple[int, ...] = ()
    rows: int = 0

    @property
    def stopped_by(self) -> PageCheck | None:
        return self.checks[-1] if self.status not in {"ok", "empty"} and self.checks else None


def search_last_page(check: Callable[[int], PageCheck], start: int, limit: int) -> SearchResult:
    """The last page with items between ``start`` and ``limit``, from as few pages as possible."""
    checks: list[PageCheck] = []

    def look(page: int) -> PageCheck:
        result = check(page)
        checks.append(result)
        return result

    def stopped(at: PageCheck, last: int | None) -> SearchResult:
        return SearchResult(
            at.kind.value, last, f"{at.kind.value}_on_page_{at.page}:{at.reason}", tuple(checks)
        )

    first = look(start)
    if first.kind is PageKind.EMPTY:
        return SearchResult("empty", None, "first_page_empty", tuple(checks))
    if first.kind is not PageKind.ITEMS:
        return stopped(first, None)
    last = start
    if start >= limit:
        return SearchResult("ok", last, "configured_last_page_reached", tuple(checks))

    step = 1
    while True:
        page = max(min(start + step, limit), min(last + 1, limit))
        result = look(page)
        if result.kind is PageKind.ITEMS:
            last = page
            if page >= limit:
                return SearchResult("ok", last, "configured_last_page_reached", tuple(checks))
            step *= 2
            continue
        if result.kind is PageKind.EMPTY:
            empty = page
            break
        return stopped(result, last)

    low, high = last + 1, empty - 1
    while low <= high:
        page = (low + high) // 2
        result = look(page)
        if result.kind is PageKind.ITEMS:
            last, low = page, page + 1
        elif result.kind is PageKind.EMPTY:
            high = page - 1
        else:
            return stopped(result, last)
    return SearchResult("ok", last, f"empty_after_page_{last}", tuple(checks))


PageReader = Callable[[int], tuple[PageKind, str] | None]


class PageChecker:
    """Opens one catalog page and says whether it has items, confirming empties and unknowns.

    ``read_items`` gets each page found with items while it is open, so its
    items need no second visit; it may overrule the page's kind, e.g. when the
    page repeats an earlier one.
    """

    def __init__(
        self,
        navigator: Navigator,
        page_url: Callable[[int], str],
        settings: Discovery,
        pacer: Pacer,
        *,
        read_items: PageReader | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.navigator = navigator
        self.read_items = read_items
        self.page_url = page_url
        self.settings = settings
        self.pacer = pacer
        self._clock = clock

    def __call__(self, page: int) -> PageCheck:
        settings = self.settings
        unknown = 0
        while True:
            result = self._once(page)
            if result.kind is PageKind.EMPTY:
                for _ in range(settings.empty_confirmations - 1):
                    result = self._once(page)
                    if result.kind is not PageKind.EMPTY:
                        break
                if result.kind is PageKind.EMPTY:
                    return result
            if result.kind is not PageKind.UNKNOWN or unknown >= settings.unknown_retries:
                return result
            unknown += 1

    def _once(self, page: int) -> PageCheck:
        url = self.page_url(page)
        outcome = self.navigator.open(url, PageType.LIST, allow_reload=False)
        deadline = self._clock() + self.settings.probe_wait_seconds
        while outcome.state in {PageState.UNKNOWN, PageState.LOADING} and self._clock() < deadline:
            self.pacer.pause(max(self.settings.settle_seconds, 0.5))
            outcome = self.navigator.wait(PageType.LIST, allow_reload=False)
        result = self._classify(page, outcome)
        if result.kind is PageKind.ITEMS and self.read_items is not None:
            overruled = self.read_items(page)
            if overruled is not None:
                result = PageCheck(page, overruled[0], outcome.state, overruled[1])
        logger.info("Discovery page %s: %s (%s)", page, result.kind.value, result.reason)
        return result

    def _classify(self, page: int, outcome: PageOutcome) -> PageCheck:
        kind = KINDS.get(outcome.state, PageKind.UNKNOWN)
        if kind is PageKind.ITEMS and outcome.items_count == 0:
            kind = PageKind.EMPTY
        if kind is PageKind.UNKNOWN and self.settings.empty_when_no_items_text_markers:
            # Recommendation blocks also sit below real results, so only a page
            # that stayed without items is read as the end of the catalog.
            text = self.navigator.page.body_text()
            marker = next(
                (
                    marker
                    for marker in self.settings.empty_when_no_items_text_markers
                    if marker.strip().lower() in text.lower()
                ),
                None,
            )
            if marker is not None:
                return PageCheck(page, PageKind.EMPTY, outcome.state, f"recommendations:{marker}")
        return PageCheck(page, kind, outcome.state, outcome.reason)

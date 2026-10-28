from __future__ import annotations

import time

from playwright.sync_api import Locator as PlaywrightLocator

from dailyscraper.pages import Selector, SelectorKind
from dailyscraper.pages.selectors import Locator
from dailyscraper.transport.errors import is_browser_closed_error
from dailyscraper.transport.page import BrowserPage

POLL_INTERVAL_MS = 100


class Element:
    def __init__(self, locator: PlaywrightLocator, finder: ElementFinder) -> None:
        self.locator = locator.first
        self._finder = finder

    def text(self) -> str:
        settings = self._finder.page.settings
        return self._finder.guard(
            lambda: self.locator.inner_text(timeout=settings.text_timeout_ms), ""
        )

    def attribute(self, name: str) -> str | None:
        settings = self._finder.page.settings
        return self._finder.guard(
            lambda: self.locator.get_attribute(name, timeout=settings.attribute_timeout_ms),
            None,
        )

    def is_visible(self) -> bool:
        return self._finder.guard(self.locator.is_visible, False)

    def click(self) -> None:
        self.locator.click(timeout=self._finder.page.settings.action_timeout_ms)

    def hover(self) -> None:
        self.locator.hover(timeout=self._finder.page.settings.action_timeout_ms)

    def type(self, value: str) -> None:
        settings = self._finder.page.settings
        self.locator.click(timeout=settings.action_timeout_ms)
        self.locator.press_sequentially(
            value, delay=settings.typing_delay_ms, timeout=settings.action_timeout_ms
        )

    def scroll_into_view(self) -> None:
        self.locator.scroll_into_view_if_needed(
            timeout=self._finder.page.settings.action_timeout_ms
        )

    def find(self, selector: Selector, wait: float = 0) -> Element | None:
        return self._finder.find(selector, wait, scope=self)

    def find_all(self, selector: Selector, wait: float = 0) -> list[Element]:
        return self._finder.find_all(selector, wait, scope=self)


class ElementFinder:
    """Locates elements by trying a selector's alternatives in order.

    All alternatives share one wait: they are polled together until one of them
    matches or the wait (the selector's own ``wait`` or the caller's default)
    runs out.
    """

    def __init__(self, page: BrowserPage) -> None:
        self.page = page

    def find(
        self, selector: Selector, wait: float = 0, *, scope: Element | None = None
    ) -> Element | None:
        matched = self._first_matching(selector, wait, scope)
        return Element(matched, self) if matched is not None else None

    def find_all(
        self, selector: Selector, wait: float = 0, *, scope: Element | None = None
    ) -> list[Element]:
        matched = self._first_matching(selector, wait, scope)
        if matched is None:
            return []
        count = self.guard(matched.count, 0)
        return [Element(matched.nth(index), self) for index in range(count)]

    def count(self, selector: Selector, *, scope: Element | None = None) -> int:
        matched = self._first_matching(selector, 0, scope)
        return self.guard(matched.count, 0) if matched is not None else 0

    def is_visible(self, selector: Selector, *, scope: Element | None = None) -> bool:
        matched = self._first_matching(selector, 0, scope)
        return matched is not None and self.guard(matched.first.is_visible, False)

    def guard[T](self, read, default: T) -> T:
        try:
            return read()
        except Exception as error:
            if is_browser_closed_error(error):
                raise
            return default

    def _first_matching(
        self, selector: Selector, wait: float, scope: Element | None
    ) -> PlaywrightLocator | None:
        effective_wait = selector.wait if selector.wait is not None else wait
        root = scope.locator if scope is not None else self.page.playwright_page
        candidates = [_locate(root, locator, selector.parent_text) for locator in selector.locators]
        deadline = time.monotonic() + effective_wait
        while True:
            for candidate in candidates:
                if self.guard(candidate.count, 0) > 0:
                    return candidate
            if time.monotonic() >= deadline:
                return None
            self.page.wait(POLL_INTERVAL_MS)


def _locate(root, locator: Locator, parent_text: str | None) -> PlaywrightLocator:
    if locator.kind is SelectorKind.CSS:
        return root.locator(locator.value)
    xpath = locator.value
    if parent_text:
        parent = xpath[: xpath.rfind("/")]
        text = parent_text.strip()
        xpath = f"{parent}/../*[text()[contains(.,'{text}')]]/ancestor::html{xpath}"
    return root.locator(f"xpath={xpath}")

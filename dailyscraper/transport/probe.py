from __future__ import annotations

import json
from typing import Any

from dailyscraper.pages import Selector
from dailyscraper.transport.elements import ElementFinder
from dailyscraper.transport.page import BrowserPage

MAX_JSON_LD_SCRIPTS = 30


class LivePageProbe:
    def __init__(self, page: BrowserPage, finder: ElementFinder, *, ignore_status: bool) -> None:
        self._page = page
        self._finder = finder
        self._ignore_status = ignore_status

    @property
    def url(self) -> str:
        return self._finder.guard(lambda: self._page.url, "")

    @property
    def http_status(self) -> int | None:
        navigation = self._page.last_navigation
        if self._ignore_status or navigation is None:
            return None
        return navigation.status

    def title(self) -> str:
        return self._page.title()

    def body_text(self) -> str:
        return self._page.body_text()

    def is_visible(self, selector: Selector) -> bool:
        return self._finder.is_visible(selector)

    def count(self, selector: Selector) -> int:
        return self._finder.count(selector)

    def json_ld_types(self) -> set[str]:
        scripts = self._page.playwright_page.locator("script[type='application/ld+json']")
        found: set[str] = set()
        count = min(self._finder.guard(scripts.count, 0), MAX_JSON_LD_SCRIPTS)
        for index in range(count):
            raw = self._finder.guard(lambda i=index: scripts.nth(i).text_content(timeout=500), "")
            try:
                _collect_types(json.loads(raw or "null"), found)
            except (TypeError, ValueError):
                continue
        return found


def _collect_types(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        raw_type = value.get("@type")
        for item in raw_type if isinstance(raw_type, list) else [raw_type]:
            if item:
                found.add(str(item).strip().lower())
        for nested in value.values():
            _collect_types(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect_types(nested, found)

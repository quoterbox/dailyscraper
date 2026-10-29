"""Whether a catalog page repeats pages already read: one rule for every way pages are opened."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from dailyscraper.pages import Repeats

ItemKey = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class RepeatCheck:
    repeated: bool
    matched: int = 0
    total: int = 0
    page: int | None = None

    @property
    def reason(self) -> str:
        return f"repeats_page_{self.page}"


class RepeatDetector:
    """Compares a page's item keys with the previous page or with every page remembered."""

    def __init__(self, settings: Repeats, key: ItemKey, *, compare_with: str | None = None) -> None:
        self.settings = settings
        self.key = key
        self.compare_with = compare_with or settings.compare_with
        self._pages: list[tuple[int, frozenset[str]]] = []

    def check(self, items: Iterable[dict[str, Any]]) -> RepeatCheck:
        keys = [self.key(item) for item in items]
        if not self.settings.enabled or not self._pages or not keys:
            return RepeatCheck(False)
        pages = self._pages[-1:] if self.compare_with == "previous" else self._pages
        known = frozenset().union(*(page_keys for _, page_keys in pages))
        matched = sum(1 for key in keys if key in known)
        closest = max(pages, key=lambda page: len(page[1].intersection(keys)))[0]
        return RepeatCheck(self._repeated(matched, len(keys)), matched, len(keys), closest)

    def remember(self, page: int, items: Iterable[dict[str, Any]]) -> None:
        self._pages.append((page, frozenset(self.key(item) for item in items)))

    def _repeated(self, matched: int, total: int) -> bool:
        match = self.settings.match
        if match == "all":
            return matched == total
        if match == "any":
            return matched > 0
        return matched / total >= self.settings.min_share

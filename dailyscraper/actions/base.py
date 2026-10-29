from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from dailyscraper.domain import PageOutcome, PageType
from dailyscraper.transport import BrowserPage, ElementFinder


class ScraperActions:
    """Hooks a scraper package overrides in its ``actions.py`` as ``class Actions``.

    Every hook is a no-op by default. ``check_interrupted`` must be called by
    long-running hooks so a stop request is honoured.
    """

    def __init__(
        self,
        page: BrowserPage,
        finder: ElementFinder,
        options: Mapping[str, Any],
        check_interrupted: Callable[[], None],
    ) -> None:
        self.page = page
        self.finder = finder
        self.options = options
        self.check_interrupted = check_interrupted

    def before_run(self) -> None:
        pass

    def after_run(self) -> None:
        pass

    def bootstrap_version(self) -> str | None:
        return None

    def validate_bootstrap(self, url: str) -> bool | None:
        return None

    def bootstrap(self, url: str) -> bool:
        return False

    def probe_bootstrap_state(self) -> PageOutcome | None:
        return None

    def filter_links(self, links: list[str]) -> list[str]:
        return links

    def before_login(self, url: str) -> None:
        pass

    def after_login(self) -> None:
        pass

    def after_first_open(self, url: str) -> None:
        pass

    def before_list(self, url: str) -> None:
        pass

    def after_list(self, items: list[dict[str, Any]]) -> None:
        pass

    def prepare_list_item(self, item: dict[str, Any], url: str) -> dict[str, Any] | None:
        return item

    def before_detail(self, item: dict[str, Any]) -> None:
        pass

    def after_detail(self, item: dict[str, Any]) -> None:
        pass

    def classify_page_state(self, page_type: PageType, outcome: PageOutcome) -> PageOutcome:
        return outcome

    def prepare_output(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return items

    def collect_list(self, url: str) -> list[dict[str, Any]] | None:
        return None

    def collect_from_input(self, items: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        return None

    def item_key(self, item: dict[str, Any]) -> str | None:
        """The item's own key for telling repeated catalog pages apart, e.g. a product id."""
        return None

    def discover_last_page(self, url: str) -> int | None:
        """The link's last catalog page found by the package itself, e.g. from an API."""
        return None

    def run_warnings(self) -> list[dict[str, Any]]:
        return []

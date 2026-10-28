from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from dailyscraper.settings.models import PlaywrightSettings
from dailyscraper.transport.errors import is_browser_closed_error


@dataclass(frozen=True)
class Navigation:
    requested_url: str
    final_url: str
    status: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class BrowserPage:
    def __init__(self, page: Page, settings: PlaywrightSettings) -> None:
        self._page = page
        self.settings = settings
        self.last_navigation: Navigation | None = None

    @property
    def playwright_page(self) -> Page:
        """Raw Playwright page for package hooks that need the full API."""
        return self._page

    @property
    def url(self) -> str:
        return self._page.url

    def open(self, url: str) -> Navigation:
        return self._navigate(
            url,
            lambda: self._page.goto(
                url,
                wait_until=self.settings.wait_until,
                timeout=self.settings.navigation_timeout_ms,
            ),
        )

    def reload(self) -> Navigation:
        return self._navigate(
            self._page.url,
            lambda: self._page.reload(
                wait_until=self.settings.wait_until,
                timeout=self.settings.navigation_timeout_ms,
            ),
        )

    def forget_navigation_status(self) -> None:
        if self.last_navigation is not None:
            self.last_navigation = Navigation(
                requested_url=self.last_navigation.requested_url,
                final_url=self._page.url,
                status=None,
            )

    def title(self) -> str:
        return self._safe(lambda: self._page.title(), "")[:300]

    def body_text(self) -> str:
        return self._safe(
            lambda: self._page.locator("body").inner_text(timeout=2_000),
            "",
        )

    def wait(self, milliseconds: float) -> None:
        if milliseconds > 0:
            self._page.wait_for_timeout(milliseconds)

    def scroll_by(self, delta_x: int, delta_y: int) -> None:
        self._page.mouse.wheel(delta_x, delta_y)

    def scroll_to_bottom(self) -> None:
        self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    def _navigate(self, url: str, action) -> Navigation:
        try:
            response = action()
        except Exception as error:
            if is_browser_closed_error(error):
                raise
            self.last_navigation = Navigation(url, self._page.url, None, str(error))
            return self.last_navigation
        status = response.status if response is not None else None
        self.last_navigation = Navigation(url, self._page.url, status)
        return self.last_navigation

    @staticmethod
    def _safe[T](read, default: T) -> T:
        try:
            return read()
        except Exception as error:
            if is_browser_closed_error(error):
                raise
            return default

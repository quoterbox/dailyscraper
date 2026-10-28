from __future__ import annotations

import logging
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Playwright

from dailyscraper.settings.models import BrowserSettings
from dailyscraper.transport.elements import ElementFinder
from dailyscraper.transport.page import BrowserPage
from dailyscraper.transport.runtime import start_playwright
from dailyscraper.transport.stealth import STEALTH_SCRIPT

logger = logging.getLogger(__name__)

REUSABLE_START_URLS = {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}


class PlaywrightBrowser:
    def __init__(self, settings: BrowserSettings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: BrowserPage | None = None
        self.finder: ElementFinder | None = None

    def __enter__(self) -> PlaywrightBrowser:
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _start(self) -> None:
        options = self.settings.playwright
        self._playwright = start_playwright()
        launch: dict[str, Any] = {
            "headless": options.headless,
            "slow_mo": options.slow_mo_ms,
            "args": list(self.settings.args),
            "ignore_default_args": ["--enable-automation"],
        }
        if options.channel:
            launch["channel"] = options.channel
        self._browser = self._playwright.chromium.launch(**launch)

        context: dict[str, Any] = {"accept_downloads": True}
        if options.viewport is None:
            context["no_viewport"] = True
        else:
            context["viewport"] = options.viewport.model_dump()
        for name in ("locale", "timezone_id"):
            if getattr(options, name) is not None:
                context[name] = getattr(options, name)
        if self.settings.user_agent:
            context["user_agent"] = self.settings.user_agent
        self._context = self._browser.new_context(**context)
        self._context.set_default_timeout(options.default_timeout_ms)
        self._context.set_default_navigation_timeout(options.navigation_timeout_ms)
        if self.settings.stealth.enabled:
            self._context.add_init_script(STEALTH_SCRIPT)

        page = (
            next((page for page in self._context.pages if page.url in REUSABLE_START_URLS), None)
            or self._context.new_page()
        )
        self.page = BrowserPage(page, options)
        self.finder = ElementFinder(self.page)
        logger.info(
            "Browser started: channel=%s headless=%s",
            options.channel or "bundled-chromium",
            options.headless,
        )

    def close(self) -> None:
        for closer in (
            self._context.close if self._context else None,
            self._browser.close if self._browser else None,
            self._playwright.stop if self._playwright else None,
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                logger.debug("Ignoring an error while closing the browser", exc_info=True)
        self._context = self._browser = self._playwright = None
        self.page = self.finder = None

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Playwright

from dailyscraper.domain import Proxy
from dailyscraper.settings.models import BrowserSettings
from dailyscraper.transport import mv3
from dailyscraper.transport.elements import ElementFinder
from dailyscraper.transport.page import BrowserPage
from dailyscraper.transport.profiles import prepare_profile
from dailyscraper.transport.resources import ResourceBlocker
from dailyscraper.transport.runtime import start_playwright
from dailyscraper.transport.stealth import STEALTH_SCRIPT

logger = logging.getLogger(__name__)

REUSABLE_START_URLS = {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}
DIRECT_IDENTITY = "direct"


@dataclass(frozen=True)
class SessionPlan:
    """How one browser session reaches the internet and where it keeps its state.

    Without ``profile_dir`` the session is ephemeral and authenticates the proxy
    natively; with it the session is persistent and uses the MV3 extension
    written under ``extension_dir``.
    """

    proxy: Proxy | None = None
    profile_dir: Path | None = None
    extension_dir: Path | None = None


class PlaywrightBrowser:
    def __init__(self, settings: BrowserSettings, plan: SessionPlan | None = None) -> None:
        self.settings = settings
        self.plan = plan or SessionPlan()
        self.blocker = ResourceBlocker(settings.resource_blocking)
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
        plan = self.plan
        self._playwright = start_playwright()
        launch: dict[str, Any] = {
            "headless": options.headless,
            "slow_mo": options.slow_mo_ms,
            "args": list(self.settings.args),
            "ignore_default_args": ["--enable-automation"],
        }
        if options.channel:
            launch["channel"] = options.channel
        context = self._context_options()

        if plan.profile_dir is None:
            if plan.proxy is not None:
                launch["proxy"] = _playwright_proxy(plan.proxy)
            self._browser = self._playwright.chromium.launch(**launch)
            self._context = self._browser.new_context(**context)
        else:
            self._context = self._launch_persistent(launch, context)

        self._context.set_default_timeout(options.default_timeout_ms)
        self._context.set_default_navigation_timeout(options.navigation_timeout_ms)
        self.blocker.install(self._context)
        if self.settings.stealth.enabled:
            self._context.add_init_script(STEALTH_SCRIPT)
        page = (
            next((page for page in self._context.pages if page.url in REUSABLE_START_URLS), None)
            or self._context.new_page()
        )
        self.page = BrowserPage(page, options)
        self.finder = ElementFinder(self.page)
        logger.info(
            "Browser started: channel=%s headless=%s proxy=%s profile=%s",
            launch.get("channel", "bundled-chromium"),
            options.headless,
            plan.proxy.proxy_id if plan.proxy else DIRECT_IDENTITY,
            plan.profile_dir or "-",
        )

    def _launch_persistent(self, launch: dict[str, Any], context: dict[str, Any]) -> BrowserContext:
        plan = self.plan
        identity = plan.proxy.identity() if plan.proxy else DIRECT_IDENTITY
        prepare_profile(
            plan.profile_dir,
            identity,
            reset_on_change=self.settings.profiles.reset_on_identity_change,
        )
        extension = None
        if plan.proxy is not None:
            if plan.extension_dir is None:
                raise ValueError("a persistent session with a proxy needs an extension directory")
            extension = mv3.write_extension(plan.extension_dir, plan.proxy)
            launch["args"] = [*launch["args"], *mv3.launch_args(extension)]
            if launch["headless"] and "channel" not in launch:
                # The headless shell cannot load extensions; the full Chromium can.
                launch["channel"] = "chromium"
        persistent = self._playwright.chromium.launch_persistent_context(
            str(plan.profile_dir), **launch, **context
        )
        self._context = persistent
        if extension is not None:
            mv3.wait_until_ready(
                persistent,
                plan.proxy,
                timeout_ms=self.settings.mv3.readiness_timeout_ms,
                settle_ms=self.settings.mv3.settle_ms,
            )
        return persistent

    def _context_options(self) -> dict[str, Any]:
        options = self.settings.playwright
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
        return context

    def close(self) -> None:
        if self.blocker.blocked:
            logger.info("Blocked resources: %s", dict(self.blocker.blocked))
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


def _playwright_proxy(proxy: Proxy) -> dict[str, str]:
    settings = {"server": proxy.server}
    if proxy.user:
        settings["username"] = proxy.user
        settings["password"] = proxy.password or ""
    return settings

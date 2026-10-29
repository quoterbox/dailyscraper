from __future__ import annotations

import logging
from dataclasses import replace

from dailyscraper.actions import ScraperActions
from dailyscraper.detection import (
    Intervention,
    StatePolicy,
    StateWaiter,
    build_policy,
    state_for_navigation_error,
)
from dailyscraper.domain import PageOutcome, PageType
from dailyscraper.execution.timing import Pacer
from dailyscraper.pages import PageStateRules, compile_list_template
from dailyscraper.schema import ScraperDefinition
from dailyscraper.settings.models import BrowserSettings
from dailyscraper.testing.fault_injection import (
    FaultPoint,
    InjectedHttpStatus,
    InjectedNavigationTimeout,
    get_fault_injector,
)
from dailyscraper.transport import (
    BrowserPage,
    ElementFinder,
    LivePageProbe,
    Navigation,
    is_timeout_error,
)

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = (2.0, 5.0)


class Navigator:
    def __init__(
        self,
        *,
        page: BrowserPage,
        finder: ElementFinder,
        definition: ScraperDefinition,
        actions: ScraperActions,
        browser: BrowserSettings,
        pacer: Pacer,
        intervention: Intervention | None = None,
    ) -> None:
        self.page = page
        self.finder = finder
        self.definition = definition
        self.actions = actions
        self.browser = browser
        self.pacer = pacer
        self._warmed_up = False
        self._bootstrapped_versions: set[str] = set()
        self._policies: dict[PageType, StatePolicy] = {}
        self.waiter = StateWaiter(
            control=page,
            make_probe=lambda ignore_status: LivePageProbe(
                page, finder, ignore_status=ignore_status
            ),
            hook=actions.classify_page_state,
            check_interrupted=pacer.check_interrupted,
            intervention=intervention,
        )

    def open(self, url: str, page_type: PageType, *, allow_reload: bool = True) -> PageOutcome:
        navigation = self.navigate(url)
        if not navigation.ok:
            return self.failed_open(url, page_type, navigation.error)
        return self.wait(page_type, allow_reload=allow_reload)

    def navigate(self, url: str) -> Navigation:
        self.warmup()
        navigation = self._open_with_attempts(url)
        if navigation.ok and self._bootstrap(url):
            navigation = self._open_with_attempts(url)
        return navigation

    def wait(self, page_type: PageType, *, allow_reload: bool = True) -> PageOutcome:
        return self.waiter.wait_for(self.policy(page_type), allow_reload=allow_reload)

    def failed_open(self, url: str, page_type: PageType, error: str | None) -> PageOutcome:
        return PageOutcome(
            state=state_for_navigation_error(error),
            page_type=page_type,
            url=url,
            reason=f"open_failed:{error}",
        )

    def warmup(self) -> None:
        if self._warmed_up:
            return
        self._warmed_up = True
        navigation = self.definition.navigation
        for url in navigation.warmup_urls:
            logger.info("Warmup open: %s", url)
            if self._open_with_attempts(url).ok:
                self.pacer.delay(navigation.delay_after_warmup)
            else:
                logger.warning("Warmup URL failed: %s", url)

    def policy(self, page_type: PageType) -> StatePolicy:
        if page_type not in self._policies:
            self._policies[page_type] = self._build_policy(page_type)
        return self._policies[page_type]

    def _build_policy(self, page_type: PageType) -> StatePolicy:
        common = self.definition.page_state or PageStateRules()
        pages = self.definition.pages
        if page_type is PageType.LIST and pages.list is not None:
            compiled = [compile_list_template(template) for template in pages.list.templates]
            return build_policy(
                common.overridden_by(pages.list.state),
                page_type,
                ready=tuple(t.template.ready for t in compiled if t.template.ready),
                items=tuple(t.items.item for t in compiled),
                empty=tuple(t.template.stop for t in compiled if t.template.stop),
            )
        if page_type is PageType.DETAIL and pages.detail is not None:
            return build_policy(
                common.overridden_by(pages.detail.state),
                page_type,
                ready=tuple(t.ready for t in pages.detail.templates if t.ready),
            )
        if page_type is PageType.AUTH and pages.auth is not None:
            return build_policy(
                common.overridden_by(pages.auth.state),
                page_type,
                ready=tuple(t.ready for t in pages.auth.templates if t.ready),
            )
        return build_policy(common, page_type)

    def _bootstrap(self, url: str) -> bool:
        """Run the package's session preparation once; True if the target must be reopened."""
        version = self.actions.bootstrap_version()
        if not version or version in self._bootstrapped_versions:
            return False
        if self.actions.validate_bootstrap(url) is True:
            self._bootstrapped_versions.add(version)
            return False
        logger.info("Session bootstrap started: version=%s target=%s", version, url)
        reopen = bool(self.actions.bootstrap(url))
        self._bootstrapped_versions.add(version)
        return reopen

    def _open_with_attempts(self, url: str) -> Navigation:
        injector = get_fault_injector()
        navigation = Navigation(url, url, None, "not attempted")
        for attempt in range(1, self.browser.open_attempts + 1):
            self.pacer.check_interrupted()
            logger.info("Opening %s (attempt %s/%s)", url, attempt, self.browser.open_attempts)
            try:
                injector.hit(FaultPoint.NAVIGATION_BEFORE_OPEN, url=url, attempt=attempt)
                navigation = self.page.open(url)
                if navigation.ok:
                    injector.hit(FaultPoint.NAVIGATION_AFTER_OPEN, url=url, attempt=attempt)
            except InjectedNavigationTimeout as error:
                navigation = Navigation(url, self.page.url, None, f"Timeout: {error}")
            except InjectedHttpStatus as error:
                navigation = replace(navigation, status=error.status_code)
                self.page.last_navigation = navigation
            if navigation.ok:
                return navigation
            logger.warning("Failed to open %s: %s", url, navigation.error)
            if is_timeout_error(navigation.error):
                break
            if attempt < self.browser.open_attempts:
                self.pacer.delay(RETRY_DELAY_SECONDS)
        return navigation

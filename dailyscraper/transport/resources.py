"""Drop resources no selector reads before they cost proxy traffic and memory."""

from __future__ import annotations

import logging
from collections import Counter
from fnmatch import fnmatch

from playwright.sync_api import BrowserContext, Route

from dailyscraper.settings.models import ResourceBlockingSettings
from dailyscraper.transport.errors import is_browser_closed_error

logger = logging.getLogger(__name__)

NEVER_BLOCKED = frozenset({"document"})


class ResourceBlocker:
    def __init__(self, settings: ResourceBlockingSettings) -> None:
        self.types = frozenset(settings.resource_types) - NEVER_BLOCKED
        self.block_patterns = settings.block_url_patterns
        self.allow_patterns = settings.allow_url_patterns
        self.blocked: Counter[str] = Counter()

    @property
    def active(self) -> bool:
        return bool(self.types or self.block_patterns)

    def install(self, context: BrowserContext) -> None:
        if self.active:
            context.route("**/*", self._handle)

    def should_block(self, url: str, resource_type: str) -> bool:
        if resource_type in NEVER_BLOCKED:
            return False
        if any(fnmatch(url, pattern) for pattern in self.allow_patterns):
            return False
        return resource_type in self.types or any(
            fnmatch(url, pattern) for pattern in self.block_patterns
        )

    def _handle(self, route: Route) -> None:
        request = route.request
        try:
            if self.should_block(request.url, request.resource_type):
                self.blocked[request.resource_type] += 1
                route.abort()
            else:
                route.fallback()
        except Exception as error:
            if is_browser_closed_error(error):
                return
            logger.debug("Resource route failed for %s: %s", request.url, error)

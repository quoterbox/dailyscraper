# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import quote


@dataclass(frozen=True)
class Proxy:
    """One way out to the internet.

    ``proxy_id`` is stable across runs; for a rotating gateway session the exit
    IP behind it changes with ``generation``, which is part of the credentials.
    ``allowed_sites`` keeps a proxy to those sites; a ``site_locked`` one cannot
    reach anything else, so a general network check says nothing about it.
    """

    proxy_id: str
    host: str
    port: int
    scheme: str = "http"
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    country_code: str | None = None
    source: str = "static"
    rotating: bool = False
    generation: int = 0
    allowed_sites: tuple[str, ...] = ()
    site_locked: bool = False

    def allows(self, site: str) -> bool:
        return not self.allowed_sites or site in self.allowed_sites

    @property
    def server(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def url(self) -> str:
        if not self.user:
            return self.server
        credentials = quote(self.user, safe="")
        if self.password:
            credentials += ":" + quote(self.password, safe="")
        return f"{self.scheme}://{credentials}@{self.host}:{self.port}"

    def identity(self) -> str:
        """Changes whenever the exit identity may change: address, credentials, session."""
        raw = json.dumps(
            [self.scheme, self.host, self.port, self.user, self.password, self.generation]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class HealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class ProxyHealthState:
    """Circuit breaker state of a proxy for one site, shared by all runs."""

    site: str
    proxy_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    generation: int = 0
    consecutive_failures: int = 0
    failures: int = 0
    successes: int = 0
    open_count: int = 0
    cooldown_until: str | None = None
    last_category: str | None = None
    updated_at: str | None = None

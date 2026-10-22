from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class PageType(StrEnum):
    LIST = "list"
    DETAIL = "detail"
    AUTH = "auth"
    GENERIC = "generic"


class PageState(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    NOT_FOUND = "not_found"
    CAPTCHA = "captcha"
    BLOCKED = "blocked"
    PROXY_AUTH = "proxy_auth"
    LOAD_FAILED = "load_failed"
    BLANK = "blank"
    LOADING = "loading"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageOutcome:
    state: PageState
    page_type: PageType
    url: str
    reason: str
    http_status: int | None = None
    items_count: int | None = None
    evidence: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def is_ready(self) -> bool:
        return self.state is PageState.READY

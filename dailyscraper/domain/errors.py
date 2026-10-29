from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    CAPTCHA = "captcha"
    BLOCKED = "blocked"
    PROXY_AUTH = "proxy_auth"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK = "network"
    LOAD_FAILED = "load_failed"
    BLANK = "blank"
    NOT_FOUND = "not_found"
    BROWSER_CLOSED = "browser_closed"
    INVALID_CONTENT = "invalid_content"
    FILESYSTEM = "filesystem"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class PageError(RuntimeError):
    """A problem a package's hook found on the page, handled like the same category found by
    the framework: a block or a captcha moves the task to another proxy, ``not_found`` is final.
    """

    def __init__(self, category: ErrorCategory, reason: str) -> None:
        super().__init__(f"{category.value}: {reason}")
        self.category = category
        self.reason = reason

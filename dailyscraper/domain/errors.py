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

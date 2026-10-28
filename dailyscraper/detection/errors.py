from __future__ import annotations

from dailyscraper.detection.markers import (
    BROWSER_ERROR_TEXT_MARKERS,
    CAPTCHA_TEXT_MARKERS,
    NETWORK_ERROR_MARKERS,
    PROXY_AUTH_TEXT_MARKERS,
    find_captcha_marker,
    find_marker,
    has_proxy_auth_status,
)
from dailyscraper.domain import ErrorCategory, PageState
from dailyscraper.transport.errors import is_browser_closed_error, is_timeout_error

STATE_CATEGORIES = {
    PageState.NOT_FOUND: ErrorCategory.NOT_FOUND,
    PageState.CAPTCHA: ErrorCategory.CAPTCHA,
    PageState.BLOCKED: ErrorCategory.BLOCKED,
    PageState.PROXY_AUTH: ErrorCategory.PROXY_AUTH,
    PageState.LOAD_FAILED: ErrorCategory.LOAD_FAILED,
    PageState.BLANK: ErrorCategory.BLANK,
    PageState.LOADING: ErrorCategory.TIMEOUT,
    PageState.UNKNOWN: ErrorCategory.INVALID_CONTENT,
}


def category_for_state(state: PageState, http_status: int | None = None) -> ErrorCategory | None:
    if state is PageState.BLOCKED and http_status == 429:
        return ErrorCategory.RATE_LIMITED
    return STATE_CATEGORIES.get(state)


def state_for_navigation_error(error: str | None) -> PageState:
    text = str(error or "").lower()
    if has_proxy_auth_status(text) or find_marker(text, PROXY_AUTH_TEXT_MARKERS):
        return PageState.PROXY_AUTH
    return PageState.LOAD_FAILED


def classify_error(error: BaseException | str) -> ErrorCategory:
    if isinstance(error, ConnectionError):
        return ErrorCategory.NETWORK
    if isinstance(error, OSError) and error.filename is not None:
        return ErrorCategory.FILESYSTEM
    text = str(error).lower()
    if is_browser_closed_error(error):
        return ErrorCategory.BROWSER_CLOSED
    if has_proxy_auth_status(text) or find_marker(text, PROXY_AUTH_TEXT_MARKERS):
        return ErrorCategory.PROXY_AUTH
    if find_captcha_marker(text, CAPTCHA_TEXT_MARKERS):
        return ErrorCategory.CAPTCHA
    if is_timeout_error(error):
        return ErrorCategory.TIMEOUT
    if find_marker(text, BROWSER_ERROR_TEXT_MARKERS) or find_marker(text, NETWORK_ERROR_MARKERS):
        return ErrorCategory.NETWORK
    return ErrorCategory.INTERNAL

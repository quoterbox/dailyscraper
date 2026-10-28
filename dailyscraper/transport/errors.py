from __future__ import annotations

BROWSER_CLOSED_MARKERS = (
    "targetclosederror",
    "target page, context or browser has been closed",
    "target window already closed",
    "browser has been closed",
    "browser has disconnected",
    "context has been closed",
    "page has been closed",
    "page closed",
    "not connected to devtools",
    "connection closed",
    "browser.close",
    "context.close",
)


class BrowserClosedError(RuntimeError):
    pass


def is_browser_closed_error(error: BaseException | str | None) -> bool:
    if isinstance(error, BrowserClosedError):
        return True
    text = str(error or "").lower()
    return any(marker in text for marker in BROWSER_CLOSED_MARKERS)


def is_timeout_error(error: BaseException | str | None) -> bool:
    if error is not None and type(error).__name__ == "TimeoutError":
        return True
    text = str(error or "").lower()
    return "timeout" in text or "timed out" in text

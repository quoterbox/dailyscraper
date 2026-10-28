from __future__ import annotations

import re

EMPTY_TEXT_MARKERS = (
    "не нашли товары",
    "товаров не найдено",
    "ничего не найдено",
    "ничего не нашлось",
    "по вашему запросу ничего не найдено",
)

NOT_FOUND_TEXT_MARKERS = (
    "страница не найдена",
    "такой страницы не существует",
    "товар не найден",
    "этого товара больше нет",
    "этот товар закончился",
    "товар больше не продается",
    "товар больше не продаётся",
    "товар снят с продажи",
    "этот товар недоступен",
    "товар больше недоступен",
    "this product is no longer available",
    "this item is no longer available",
    "product is no longer available",
    "item is no longer available",
    "this product has been discontinued",
    "this item has been discontinued",
)

CAPTCHA_TEXT_MARKERS = (
    "capcha",
    "captcha",
    "капча",
    "сопоставьте пазл",
    "slide the slider",
    "fit the puzzle piece",
    "puzzle piece into the outline",
    "confirm that you are not a bot",
    "confirm that you're not a bot",
    "confirm that you’re not a bot",
)

BLOCKED_TEXT_MARKERS = (
    "confirm that you",
    "not a bot",
    "antibot",
    "подозрительная активность",
    "похоже, нет соединения",
    "выключите vpn",
    "подключитесь к другой сети",
    "доступ ограничен",
    "access denied",
    "forbidden",
)

PROXY_AUTH_TEXT_MARKERS = (
    "407 proxy",
    "proxy authentication",
    "proxy authorization",
    "proxy-authenticate",
    "err_proxy_auth",
    "err_invalid_auth_credentials",
)

BROWSER_ERROR_TEXT_MARKERS = (
    "err_connection",
    "err_tunnel",
    "err_network",
    "err_internet",
    "err_proxy_connection_failed",
    "err_no_supported_proxies",
    "tunnel connection failed",
    "this site can't be reached",
    "не удается получить доступ к сайту",
)

NETWORK_ERROR_MARKERS = (
    "net::",
    "err_",
    "socket hang up",
    "econnreset",
    "connection reset",
    "connection aborted",
    "broken pipe",
)

_URLS_AND_EMAILS = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[\w-]+")
_PROXY_AUTH_STATUS = re.compile(
    r"(?<![a-z0-9])(?:status_code|status|code|http)\s*[=:]?\s*407(?!\d)"
    r"|http/\d(?:\.\d)?\s+407(?!\d)"
    r"|(?<!\d)407\s+proxy\b"
)


def find_marker(text: str, markers: tuple[str, ...]) -> str | None:
    return next((marker for marker in markers if marker in text), None)


def find_captcha_marker(text: str, markers: tuple[str, ...]) -> str | None:
    # Block pages without a challenge still mention captcha in support e-mails and links.
    return find_marker(_URLS_AND_EMAILS.sub(" ", text), markers)


def has_proxy_auth_status(text: str) -> bool:
    return bool(_PROXY_AUTH_STATUS.search(text.lower()))


def merge_markers(defaults: tuple[str, ...], configured: tuple[str, ...] | None) -> tuple[str, ...]:
    merged: list[str] = []
    for marker in (*defaults, *(configured or ())):
        normalized = marker.strip().lower()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return tuple(merged)

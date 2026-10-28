from __future__ import annotations

import re
from urllib.parse import urljoin

from dailyscraper.pages import Selector
from dailyscraper.transport import Element

_WHITESPACE = re.compile(r"\s+")
_FORBIDDEN = str.maketrans("", "", ";<>")


def read_value(
    element: Element, selector: Selector, *, base_url: str, link: bool = False
) -> str | None:
    attribute = selector.attribute or ("href" if link else None)
    value = element.attribute(attribute) if attribute else element.text()
    if value is None:
        return None
    if attribute in {"href", "src"}:
        value = urljoin(base_url, value)
    if selector.prepare is not None:
        value = selector.prepare.apply(value)
        if value is None:
            return None
    return clean_text(value)


def clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.translate(_FORBIDDEN)).strip()


def is_empty_value(value: object) -> bool:
    return value is None or str(value).strip().lower() in {"", "none"}

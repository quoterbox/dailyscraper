from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MASK = "***"
SECRET_KEY = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential", re.I
)
URL_CREDENTIALS = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)


def redact(value: Any) -> Any:
    """Copy of ``value`` with secret-looking keys masked and credentials removed from URLs."""
    if isinstance(value, Mapping):
        return {
            key: MASK if SECRET_KEY.search(str(key)) and value[key] else redact(value[key])
            for key in value
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return URL_CREDENTIALS.sub(lambda match: f"{match['scheme']}{MASK}@", value)
    return value

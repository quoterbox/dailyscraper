from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

MASK = "***"
MAX_ERROR_LENGTH = 500
_BOT_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+", re.IGNORECASE)


@dataclass(frozen=True)
class Message:
    """Plain text: Telegram markup would need escaping, and one miss turns an alert into a 400."""

    title: str
    body: str

    def as_text(self) -> str:
        return f"{self.title}\n{self.body}" if self.body else self.title


@dataclass(frozen=True)
class Sent:
    pass


@dataclass(frozen=True)
class RetryAfter:
    seconds: float
    reason: str


@dataclass(frozen=True)
class PermanentFailure:
    """The message can never be delivered as addressed."""

    reason: str


DeliveryOutcome = Sent | RetryAfter | PermanentFailure


class Channel(Protocol):
    name: str

    def send(self, message: Message) -> DeliveryOutcome: ...


class ChannelConfigurationError(ValueError):
    pass


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Error text without known secrets; the Telegram token travels inside the request URL."""
    result = str(text).replace("\n", " ")
    for secret in secrets:
        if secret:
            result = result.replace(secret, MASK).replace(quote(secret, safe=""), MASK)
    result = _BOT_TOKEN_IN_URL.sub(f"/bot{MASK}", result)
    return result[:MAX_ERROR_LENGTH]


def scrub_error(error: BaseException, secrets: tuple[str, ...] = ()) -> str:
    return scrub(f"{type(error).__name__}: {error}", secrets)

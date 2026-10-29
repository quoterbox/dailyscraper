from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from dailyscraper.observability.notifications.channels.base import (
    ChannelConfigurationError,
    DeliveryOutcome,
    Message,
    PermanentFailure,
    RetryAfter,
    Sent,
    scrub,
    scrub_error,
)

MAX_MESSAGE_LENGTH = 4096
DEFAULT_RETRY_SECONDS = 30.0
# A wrong chat, a blocked bot or a revoked token: retrying only burns the outbox.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404})


class TelegramChannel:
    """One-way ``sendMessage`` to a single chat."""

    def __init__(
        self,
        *,
        name: str,
        bot_token: str,
        chat_id: str,
        api_root: str = "https://api.telegram.org",
        timeout_seconds: float = 10.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not bot_token:
            raise ChannelConfigurationError("Telegram bot token is empty")
        if not chat_id:
            raise ChannelConfigurationError("Telegram chat id is empty")
        self.name = name
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self._token = bot_token
        self._url = f"{api_root.rstrip('/')}/bot{bot_token}/sendMessage"
        self._urlopen = urlopen

    def send(self, message: Message) -> DeliveryOutcome:
        payload = {
            "chat_id": self.chat_id,
            "text": message.as_text()[:MAX_MESSAGE_LENGTH],
            "disable_web_page_preview": True,
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout_seconds):
                return Sent()
        except urllib.error.HTTPError as error:
            return self._classify(error)
        except Exception as error:
            return RetryAfter(DEFAULT_RETRY_SECONDS, scrub_error(error, (self._token,)))

    def _classify(self, error: urllib.error.HTTPError) -> DeliveryOutcome:
        body = _json(error)
        description = scrub(str(body.get("description") or error.reason), (self._token,))
        reason = f"{error.code} {description}"
        if error.code == 429:
            retry_after = (body.get("parameters") or {}).get("retry_after")
            return RetryAfter(float(retry_after or DEFAULT_RETRY_SECONDS), reason)
        if error.code in PERMANENT_STATUSES:
            return PermanentFailure(reason)
        return RetryAfter(DEFAULT_RETRY_SECONDS, reason)


def _json(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        value = json.loads(error.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}

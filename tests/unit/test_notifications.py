from __future__ import annotations

import io
import json
import logging
import smtplib
import urllib.error
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest

from dailyscraper.domain import Event, NotificationStatus, Urgency
from dailyscraper.observability.notifications import NotificationRouter, Notifier
from dailyscraper.observability.notifications.channels import (
    EmailChannel,
    Message,
    PermanentFailure,
    RetryAfter,
    Sent,
    TelegramChannel,
    build_channels,
)
from dailyscraper.settings.models import (
    EmailChannelSettings,
    NotificationEvents,
    NotificationRule,
    NotificationsSettings,
    TelegramChannelSettings,
)
from dailyscraper.storage import open_store

TOKEN = "123456:SECRET-token"


def event(type_: str, seq: int, *, run_id: str = "r1", scraper: str | None = None, **data):
    return Event(
        event_id=uuid.uuid4().hex,
        run_id=run_id,
        project_id="default",
        type=type_,
        at="2026-10-28T10:00:00.000+00:00",
        scraper=scraper,
        data=MappingProxyType(data),
        seq=seq,
    )


def test_router_picks_events_worth_a_message() -> None:
    router = NotificationRouter(NotificationEvents(), ["telegram"])

    captcha = router.route(
        event(
            "intervention.required",
            3,
            scraper="catalog",
            url="https://shop.test/c",
            host="box",
            deadline_at="2026-10-28T10:10:00+00:00",
        )
    )
    failed = router.route(event("run.finished", 9, status="failed", error="boom"))
    finished = router.route(
        event("run.finished", 9, status="completed", rows=5, scrapers={"catalog": 5})
    )

    (notification,) = captcha
    assert notification.kind == "intervention_required"
    assert notification.urgency is Urgency.URGENT
    assert notification.notification_id == "r1:3:telegram"
    assert notification.coalesce_key == "intervention_required:r1"
    assert "url: https://shop.test/c" in notification.body
    assert "solve by: " in notification.body and "box" in notification.body
    assert failed[0].kind == "run_failed" and "error: boom" in failed[0].body
    assert finished[0].title == "Run finished: completed"
    assert "  catalog: 5 rows" in finished[0].body
    assert router.route(event("run.started", 1)) == []
    assert router.route(event("task.finished", 4)) == []


def test_router_respects_disabled_events() -> None:
    rules = NotificationEvents(run_finished=NotificationRule(enabled=False))
    router = NotificationRouter(rules, ["telegram", "email:a@b.c"])

    assert router.route(event("run.finished", 2, status="completed")) == []
    assert len(router.route(event("run.finished", 2, status="failed"))) == 2


class Opener:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(b"{}")


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        code,
        "error",
        {},
        io.BytesIO(json.dumps(body).encode()),
    )


def telegram(opener: Opener) -> TelegramChannel:
    return TelegramChannel(name="telegram", bot_token=TOKEN, chat_id="42", urlopen=opener)


def test_telegram_sends_plain_text_to_the_chat() -> None:
    opener = Opener(None)

    assert telegram(opener).send(Message("Title", "body")) == Sent()

    (request,) = opener.requests
    assert request.full_url.endswith(f"/bot{TOKEN}/sendMessage")
    assert json.loads(request.data) == {
        "chat_id": "42",
        "text": "Title\nbody",
        "disable_web_page_preview": True,
    }


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (http_error(429, {"parameters": {"retry_after": 7}}), RetryAfter),
        (http_error(403, {"description": "bot was blocked"}), PermanentFailure),
        (http_error(502, {}), RetryAfter),
        (urllib.error.URLError(f"cannot reach /bot{TOKEN}/sendMessage"), RetryAfter),
    ],
)
def test_telegram_failures_are_classified_without_the_token(failure, expected) -> None:
    outcome = telegram(Opener(failure)).send(Message("t", "b"))

    assert isinstance(outcome, expected)
    assert TOKEN not in outcome.reason
    if failure.__class__ is urllib.error.HTTPError and failure.code == 429:
        assert outcome.seconds == 7


class SMTPClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent = []
        self.calls = []

    def __call__(self, **kwargs):
        return self

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username))

    def send_message(self, email, from_addr, to_addrs):
        if self.error is not None:
            raise self.error
        self.sent.append(email)
        return {}

    def quit(self):
        self.calls.append("quit")


def email(client: SMTPClient, **kwargs) -> EmailChannel:
    return EmailChannel(
        name="email:ops@shop.test",
        host="smtp.shop.test",
        port=587,
        encryption="starttls",
        from_address="bot@shop.test",
        recipient="ops@shop.test",
        username="bot",
        password="smtp-secret",
        subject_prefix="[ds]",
        client_factory=client,
        **kwargs,
    )


def test_email_logs_in_over_tls_and_sends_to_one_recipient() -> None:
    client = SMTPClient()

    assert email(client).send(Message("Run failed", "run: r1")) == Sent()

    (sent,) = client.sent
    assert sent["Subject"] == "[ds] Run failed" and sent["To"] == "ops@shop.test"
    assert client.calls == ["ehlo", "starttls", "ehlo", ("login", "bot"), "quit"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            smtplib.SMTPRecipientsRefused({"ops@shop.test": (550, b"no such user")}),
            PermanentFailure,
        ),
        (smtplib.SMTPDataError(451, b"try later"), RetryAfter),
        (ConnectionRefusedError("smtp-secret refused"), RetryAfter),
    ],
)
def test_email_failures_are_classified_without_the_password(error, expected) -> None:
    outcome = email(SMTPClient(error)).send(Message("t", "b"))

    assert isinstance(outcome, expected)
    assert "smtp-secret" not in outcome.reason


def test_channels_without_credentials_are_skipped(caplog) -> None:
    settings = (
        TelegramChannelSettings(type="telegram"),
        EmailChannelSettings(
            type="email",
            host="smtp.shop.test",
            from_address="bot@shop.test",
            recipients=("a@shop.test", "b@shop.test"),
        ),
    )
    with caplog.at_level(logging.WARNING):
        channels = build_channels(settings, {})

    assert [channel.name for channel in channels] == ["email:a@shop.test", "email:b@shop.test"]
    assert "TELEGRAM_BOT_TOKEN" in caplog.text
    assert [
        channel.name
        for channel in build_channels(
            settings[:1], {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "1"}
        )
    ] == ["telegram"]


class FakeChannel:
    def __init__(self, name: str, *outcomes) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.messages: list[Message] = []

    def send(self, message: Message):
        self.messages.append(message)
        outcome = self.outcomes.pop(0) if self.outcomes else Sent()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def store(tmp_path: Path):
    opened = open_store("sqlite", tmp_path / "store.sqlite")
    yield opened
    opened.close()


def notifier(store, *channels, **settings) -> Notifier:
    config = NotificationsSettings(enabled=True, retry_backoff_seconds=0, **settings)
    router = NotificationRouter(config.events, [channel.name for channel in channels])
    return Notifier(store, "r1", router, {channel.name: channel for channel in channels}, config)


def test_notifier_sends_one_message_for_repeated_captchas(store) -> None:
    channel = FakeChannel("telegram")
    store.events.append(
        [
            event("run.started", 0),
            event("intervention.required", 0, scraper="a", url="https://shop.test/1"),
            event("intervention.required", 0, scraper="b", url="https://shop.test/2"),
        ]
    )

    stats = notifier(store, channel).run_once()

    assert (stats.created, stats.merged, stats.sent) == (1, 1, 1)
    (message,) = channel.messages
    assert "https://shop.test/1" in message.body
    assert message.body.endswith("(+1 more of the same while this message waited)")
    assert store.notifications.cursor("notifications:r1") == 3


def test_notifier_suppresses_a_repeat_within_the_cooldown(store) -> None:
    channel = FakeChannel("telegram")
    sender = notifier(store, channel)
    store.events.append([event("intervention.required", 0, url="https://shop.test/1")])
    sender.run_once()
    store.events.append([event("intervention.required", 0, url="https://shop.test/2")])

    stats = sender.run_once()

    assert stats.suppressed == 1 and len(channel.messages) == 1


def test_notifier_retries_then_gives_up(store) -> None:
    channel = FakeChannel("telegram", RetryAfter(0, "busy"), RuntimeError("down"))
    sender = notifier(store, channel, max_attempts=2)
    store.events.append([event("run.finished", 0, status="failed")])

    first, second = sender.run_once(), sender.run_once()

    assert (first.retried, second.failed) == (1, 1)
    (record,) = store.notifications.list("r1")
    assert record.status is NotificationStatus.FAILED
    assert record.attempts == 2 and "down" in record.last_error


def test_notifier_drops_a_permanently_rejected_message(store) -> None:
    channel = FakeChannel("telegram", PermanentFailure("403 chat not found"))
    store.events.append([event("run.finished", 0, status="completed")])

    stats = notifier(store, channel).run_once()

    assert stats.failed == 1
    assert store.notifications.list("r1")[0].last_error == "403 chat not found"


def test_notifier_stop_flushes_the_final_message(store) -> None:
    channel = FakeChannel("telegram")
    sender = notifier(store, channel, dispatch_interval_seconds=60).start()
    store.events.append([event("run.finished", 0, status="completed")])

    sender.stop()

    assert [message.title for message in channel.messages] == ["Run finished: completed"]

"""CAPTCHA waits and notifications through real slot processes and a local Telegram API."""

from __future__ import annotations

from pathlib import Path

from dailyscraper.domain import ErrorCategory, NotificationStatus, RunStatus, TaskStatus
from dailyscraper.settings.models import (
    InterventionSettings,
    ManualInterventionSettings,
    NotificationsSettings,
    TelegramChannelSettings,
)
from tests.integration.runs import make_package, output_rows, run


def notifying(settings, telegram_server, monkeypatch, *, timeout: float = 30, **runtime):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    return settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update=runtime),
            "interventions": InterventionSettings(
                manual=ManualInterventionSettings(
                    timeout_seconds=timeout, poll_interval_seconds=0.3
                )
            ),
            "notifications": NotificationsSettings(
                enabled=True,
                dispatch_interval_seconds=0.2,
                channels=(TelegramChannelSettings(type="telegram", api_root=telegram_server.url),),
            ),
        }
    )


def test_a_solved_captcha_continues_the_task_and_notifies_once(
    settings, store, project_root: Path, telegram_server, monkeypatch
) -> None:
    settings = notifying(settings, telegram_server, monkeypatch, task_deadline_seconds=1)
    name = make_package(
        settings, project_root, "catalog", "list", {"rows": 2, "captcha": {"solve_after": 6}}
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    assert len(output_rows(report, name)) == 4
    types = [event.type for event in store.events.list(report.run.run_id)]
    assert types.count("intervention.required") == 2
    assert types.count("intervention.resolved") == 2
    assert report.run.summary["scrapers"][name]["interventions"] == {
        "required": 2,
        "resolved": 2,
    }
    texts = [message["text"] for message in telegram_server.messages]
    captcha = [text for text in texts if text.startswith("CAPTCHA")]
    assert len(captcha) == 1, texts
    assert f"scraper: {name}" in captcha[0] and "url: https://a.test/c" in captcha[0]
    assert texts[-1].startswith("Run finished: completed")
    assert telegram_server.messages[0]["path"] == "/bot1:test-token/sendMessage"
    notifications = store.notifications.list(report.run.run_id)
    assert {item.status for item in notifications} == {NotificationStatus.SENT}


def test_an_unsolved_captcha_fails_the_task_as_captcha(
    settings, store, project_root: Path, telegram_server, monkeypatch
) -> None:
    settings = notifying(settings, telegram_server, monkeypatch, timeout=0.6, task_attempts=1)
    name = make_package(settings, project_root, "catalog", "list", {"captcha": {}})

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    tasks = store.tasks.list(report.run.run_id)
    assert {task.status for task in tasks} == {TaskStatus.FAILED}
    assert {task.last_category for task in tasks} == {ErrorCategory.CAPTCHA}
    types = [event.type for event in store.events.list(report.run.run_id)]
    assert types.count("intervention.timeout") == 2
    telegram_server.wait_for("CAPTCHA: manual action required")
    assert telegram_server.messages[-1]["text"].startswith("Run finished: completed_with_errors")


def test_notifications_off_send_nothing(
    settings, store, project_root: Path, telegram_server, monkeypatch
) -> None:
    settings = notifying(settings, telegram_server, monkeypatch)
    settings = settings.model_copy(
        update={"notifications": settings.notifications.model_copy(update={"enabled": False})}
    )
    name = make_package(settings, project_root, "catalog", "list", {"captcha": {"solve_after": 1}})

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    assert telegram_server.messages == []
    assert store.notifications.list(report.run.run_id) == []

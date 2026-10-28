"""Proxy allocation, health and network checks in runs with real slot processes."""

from __future__ import annotations

import pytest

from dailyscraper.domain import HealthStatus, RunStatus
from dailyscraper.output import iter_csv
from dailyscraper.settings.models import (
    ProxiesSettings,
    ProxyCheckSettings,
    ScraperSelection,
)
from tests.harness.connect_proxy import ProgrammableConnectProxy
from tests.integration.runs import make_input, make_package, run

pytestmark = pytest.mark.serial


def with_proxies(settings, proxies: dict, *, check: ProxyCheckSettings | None = None, **runtime):
    return settings.model_copy(
        update={
            "proxies": ProxiesSettings.model_validate(proxies),
            "proxy_check": check or ProxyCheckSettings(enabled=False),
            "runtime": settings.runtime.model_copy(
                update={"max_browsers_per_site_proxy": 1, **runtime}
            ),
        }
    )


def static(*names: str) -> dict:
    return {
        "source": "static",
        "static": {
            "items": [
                {"name": name, "host": "10.255.0.1", "port": 9000 + number}
                for number, name in enumerate(names, start=1)
            ]
        },
    }


def test_parallel_slots_get_different_proxies(settings, store, project_root, scenario_workspace):
    name = make_package(settings, project_root, "details", "detail", {"row_delay": 0.3})
    settings = with_proxies(settings, static("p1", "p2", "p3")).model_copy(
        update={"scrapers": (ScraperSelection(name=name, max_parallel=3),)}
    )

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 6))

    assert report.run.status is RunStatus.COMPLETED
    assert set(report.run.summary["scrapers"][name]["proxies"]) == {"p1", "p2", "p3"}
    assert report.run.summary["slots"] == 3


def test_blocked_proxy_is_opened_and_the_task_moves_on(settings, store, project_root):
    fake = {"rows": 2, "proxies": {"p1": {"rows": 0, "category": "blocked"}}}
    name = make_package(settings, project_root, "catalog", "list", fake)
    settings = with_proxies(settings, static("p1", "p2"))

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    rows = [row for path in report.outputs["csv"] for row in iter_csv(path)]
    assert len(rows) == 4 and {row["proxy"] for row in rows} == {"p2"}
    health = store.proxy_health.states("dailyscraper.com", ["p1", "p2"])
    assert health["p1"].status is HealthStatus.OPEN
    assert health["p2"].status is HealthStatus.HEALTHY
    events = store.events.list(report.run.run_id, types=["proxy.health"])
    assert any(event.data["proxy"] == "p1" and event.data["status"] == "open" for event in events)

    next_run = run(settings, store, name)

    assert next_run.run.summary["scrapers"][name]["proxies"] == {"p2": 2}


def test_gateway_session_rotates_instead_of_resting(settings, store, project_root):
    fake = {"rows": 1, "proxies": {"res_01": {"rows": 0, "category": "captcha"}}}
    name = make_package(settings, project_root, "catalog", "list", fake)
    gateway = {
        "source": "gateway",
        "gateway": {
            "host": "10.255.0.2",
            "port": 10000,
            "sessions": 2,
            "user": "acc",
            "password": "pw",
        },
    }
    settings = with_proxies(settings, gateway)

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    first = store.proxy_health.states("dailyscraper.com", ["res_01"])["res_01"]
    assert first.generation >= 1 and first.status is HealthStatus.UNKNOWN


def test_network_check_keeps_bad_proxies_out(
    settings,
    store,
    project_root,
    ip_server,
) -> None:
    name = make_package(settings, project_root, "catalog", "list", {"rows": 1})
    with (
        ProgrammableConnectProxy("tunnel", credentials=("u", "pw")) as good,
        ProgrammableConnectProxy("proxy_auth") as bad,
    ):
        proxies = {
            "source": "static",
            "static": {
                "default_auth": {"user": "u", "password": "pw"},
                "items": [
                    {"name": "bad", "host": bad.endpoint.host, "port": bad.endpoint.port},
                    {"name": "good", "host": good.endpoint.host, "port": good.endpoint.port},
                ],
            },
        }
        settings = with_proxies(
            settings, proxies, check=ProxyCheckSettings(url=ip_server, timeout_seconds=5)
        )

        report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    assert set(report.run.summary["scrapers"][name]["proxies"]) == {"good"}
    checked = {
        event.data["proxy"]: event.data
        for event in store.events.list(report.run.run_id, types=["proxy.checked"])
    }
    assert checked["good"]["ok"] and checked["good"]["exit_ip"] == "203.0.113.7"
    assert checked["bad"]["category"] == "proxy_auth"
    bad_health = store.proxy_health.states("dailyscraper.com", ["bad"])["bad"]
    assert bad_health.status is HealthStatus.OPEN


def test_selected_proxies_limit_a_scraper(settings, store, project_root, scenario_workspace):
    name = make_package(settings, project_root, "details", "detail", {})
    settings = with_proxies(settings, static("p1", "p2", "p3")).model_copy(
        update={"scrapers": (ScraperSelection(name=name, max_parallel=3, proxy_names=("p3",)),)}
    )

    report = run(settings, store, name, input_path=make_input(scenario_workspace / "in.csv", 4))

    assert report.run.status is RunStatus.COMPLETED
    assert set(report.run.summary["scrapers"][name]["proxies"]) == {"p3"}

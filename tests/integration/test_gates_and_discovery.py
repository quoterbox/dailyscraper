"""Health probes, gates, discovery and proxy thresholds through real slot processes."""

from __future__ import annotations

from dailyscraper.domain import Proxy, RunStatus, TaskKind, TaskStatus
from dailyscraper.output import iter_csv
from dailyscraper.proxies import ProxyHealth
from dailyscraper.settings.models import ProxiesSettings, ProxyCheckSettings
from tests.integration.runs import make_package, run

LINKS = ["https://a.test/c/?sort=price", "https://a.test/c/?sort=new"]


def discovering(**discovery):
    def edit(definition: dict) -> None:
        definition["links"] = [{"id": "main", "type": "list", "urls": LINKS}]
        definition["discovery"] = {"enabled": True, **discovery}
        definition["pages"]["list"]["pagination"]["last_page"] = 0

    return edit


def checked(blocking: bool = False, website: str | None = None):
    def edit(definition: dict) -> None:
        definition["health_check"] = {"blocking": blocking, "required_fields": ["item_name"]}
        if website:
            definition["website"] = website

    return edit


def with_runtime(settings, **runtime):
    return settings.model_copy(update={"runtime": settings.runtime.model_copy(update=runtime)})


def all_rows(report) -> list[dict[str, str]]:
    return [row for path in report.outputs["csv"] for row in iter_csv(path)]


def test_discovery_keeps_the_pages_it_opened(settings, store, project_root) -> None:
    settings = with_runtime(settings, pages_per_task=2)
    name = make_package(
        settings, project_root, "catalog", "list", {"rows": 1, "last_page": 5}, discovering()
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    tasks = store.tasks.list(report.run.run_id)
    assert [task.kind for task in tasks].count(TaskKind.DISCOVER) == 1
    ranges = sorted(
        (task.payload["link"], tuple(task.payload["pages"]))
        for task in tasks
        if task.kind is TaskKind.LIST
    )
    # The search opened 1, 2, 3, 5 of the probed link: only page 4 is left there.
    assert ranges == sorted(
        [(LINKS[0], (4, 4)), (LINKS[1], (1, 2)), (LINKS[1], (3, 4)), (LINKS[1], (5, 5))]
    )
    probed = sorted(
        row["pages"] for row in all_rows(report) if row["item_name"].startswith(LINKS[0])
    )
    assert probed == ["1-1", "2-2", "3-3", "4-4", "5-5"]
    (found,) = report.run.summary["scrapers"][name]["discovery"]
    assert (found["status"], found["last_page"], found["saved_pages"]) == ("ok", 5, [1, 2, 3, 5])


def test_a_short_catalog_needs_no_list_tasks(settings, store, project_root) -> None:
    def single(definition: dict) -> None:
        discovering()(definition)
        definition["links"][0]["urls"] = LINKS[:1]

    name = make_package(
        settings, project_root, "catalog", "list", {"rows": 2, "last_page": 3}, single
    )

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    kinds = [task.kind for task in store.tasks.list(report.run.run_id)]
    assert kinds == [TaskKind.DISCOVER]
    assert len(all_rows(report)) == 6


def test_blocked_discovery_retries_then_follows_its_policy(settings, store, project_root) -> None:
    fake = {
        "rows": 1,
        "last_page": 2,
        "attempts": {"1": {"discover": {"status": "blocked", "last_page": None}}},
    }
    name = make_package(settings, project_root, "catalog", "list", fake, discovering())

    report = run(with_runtime(settings, pages_per_task=2), store, name)

    assert report.run.status is RunStatus.COMPLETED
    (discover,) = [t for t in store.tasks.list(report.run.run_id) if t.kind is TaskKind.DISCOVER]
    assert discover.attempts == 2
    # Pages 1–2 of the probed link come from discovery, the other link gets one task.
    assert len(all_rows(report)) == 3


def test_fail_run_policy_fails_the_run(settings, store, project_root) -> None:
    fake = {"discover": {"status": "blocked", "last_page": 3}}
    name = make_package(
        settings, project_root, "catalog", "list", fake, discovering(on_blocked="fail_run")
    )

    report = run(with_runtime(settings, task_attempts=1), store, name)

    assert report.run.status is RunStatus.FAILED
    assert report.run.error.startswith("discovery blocked")


def test_a_scraper_works_while_another_one_probes(settings, store, project_root) -> None:
    fast = make_package(settings, project_root, "fast", "list", {"rows": 1})
    slow = make_package(
        settings,
        project_root,
        "slow",
        "list",
        {"rows": 1, "probe": {"ok": True, "delay": 2}},
        checked(),
    )

    report = run(settings, store, fast, slow)

    assert report.run.status is RunStatus.COMPLETED
    events = store.events.list(report.run.run_id)
    fast_done = next(e.seq for e in events if e.type == "task.finished" and e.scraper == fast)
    slow_open = next(e.seq for e in events if e.type == "gate.opened" and e.scraper == slow)
    slow_work = [
        e.seq
        for e in events
        if e.type == "task.finished" and e.scraper == slow and "probe" not in e.data["attempt"]
    ]
    assert fast_done < slow_open < min(slow_work)
    assert report.run.summary["scrapers"][slow]["gate"]["state"] == "open"


def test_a_failed_blocking_probe_closes_only_its_scraper(settings, store, project_root) -> None:
    good = make_package(settings, project_root, "good", "list", {"rows": 1}, checked(True))
    broken = make_package(
        settings,
        project_root,
        "broken",
        "list",
        {"probe": {"ok": False, "category": "invalid_content"}},
        checked(True),
    )

    report = run(settings, store, good, broken)

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    tasks = store.tasks.list(report.run.run_id)
    assert {t.status for t in tasks if t.scraper == broken} == {
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    probe = next(t for t in tasks if t.scraper == broken and t.kind is TaskKind.PROBE)
    assert probe.attempts == 1
    assert {row["item_name"].split("#")[0] for row in all_rows(report)} == {
        "https://a.test/c1",
        "https://a.test/c2",
    }
    summary = report.run.summary["scrapers"]
    assert summary[broken]["gate"]["state"] == "closed"
    assert summary[good]["gate"]["state"] == "open"


def test_a_failed_probe_that_does_not_block_lets_the_scraper_run(
    settings, store, project_root
) -> None:
    fake = {"rows": 1, "probe": {"ok": False, "category": "invalid_content"}}
    name = make_package(settings, project_root, "catalog", "list", fake, checked(False))

    report = run(settings, store, name)

    assert report.run.status is RunStatus.COMPLETED
    gate = report.run.summary["scrapers"][name]["gate"]
    assert gate["state"] == "open" and gate["reason"].startswith("health probe failed")


def proxied(settings):
    items = [{"name": name, "host": "10.255.0.1", "port": 9001 + n} for n, name in enumerate("ab")]
    return settings.model_copy(
        update={
            "proxies": ProxiesSettings.model_validate(
                {"source": "static", "static": {"items": items}}
            ),
            "proxy_check": ProxyCheckSettings(enabled=False),
        }
    )


def burn(settings, store, site: str) -> None:
    health = ProxyHealth(store, settings.proxy_health)
    for name, port in (("a", 9001), ("b", 9002)):
        health.open_now(site, Proxy(proxy_id=name, host="10.255.0.1", port=port), "blocked")


def test_a_scraper_without_usable_proxies_is_closed(settings, store, project_root) -> None:
    settings = proxied(settings)
    good = make_package(settings, project_root, "good", "list", {"rows": 1})
    starved = make_package(
        settings, project_root, "starved", "list", {"rows": 1}, checked(website="https://b.test")
    )
    burn(settings, store, "b.test")

    report = run(settings, store, good, starved)

    assert report.run.status is RunStatus.COMPLETED_WITH_ERRORS
    gate = report.run.summary["scrapers"][starved]["gate"]
    assert gate == {"state": "closed", "reason": "0 of 2 proxies usable"}
    assert {t.status for t in store.tasks.list(report.run.run_id) if t.scraper == starved} == {
        TaskStatus.CANCELLED
    }
    assert len(all_rows(report)) == 2


def test_a_run_without_usable_proxies_fails_before_browsers(settings, store, project_root):
    settings = proxied(settings)
    name = make_package(settings, project_root, "catalog", "list", {"rows": 1})
    burn(settings, store, "dailyscraper.com")

    report = run(settings, store, name)

    assert report.run.status is RunStatus.FAILED
    assert "0 of 2 proxies usable" in report.run.error
    assert report.run.summary["slots"] == 0

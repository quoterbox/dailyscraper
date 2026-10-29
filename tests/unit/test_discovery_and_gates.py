from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.dispatch import Gates
from dailyscraper.domain import (
    AttemptStatus,
    ErrorCategory,
    PageOutcome,
    PageState,
    PageType,
    Task,
    TaskKind,
    TaskOutcome,
    TaskStatus,
)
from dailyscraper.execution.discovery import PageCheck, PageChecker, PageKind, search_last_page
from dailyscraper.execution.health import evaluate
from dailyscraper.schema import ScraperDefinition, ScraperPackage
from dailyscraper.schema.scraper import Discovery, HealthCheck
from dailyscraper.tasks.discovery import after_discovery
from dailyscraper.tasks.planner import page_count_key, page_ranges, plan_tasks
from dailyscraper.transport.page import Navigation
from dailyscraper.transport.profiles import ProfileState


def site(last: int, *, stop: dict[int, PageKind] | None = None):
    seen: list[int] = []

    def check(page: int) -> PageCheck:
        seen.append(page)
        kind = (stop or {}).get(page) or (PageKind.ITEMS if page <= last else PageKind.EMPTY)
        return PageCheck(page, kind, PageState.READY, kind.value)

    return check, seen


@pytest.mark.parametrize("last", [1, 2, 3, 13, 64, 99])
def test_search_finds_the_last_page_in_few_steps(last: int) -> None:
    check, seen = site(last)

    result = search_last_page(check, 1, 1000)

    assert (result.status, result.last_page) == ("ok", last)
    assert len(seen) <= 2 * last.bit_length() + 2


def test_search_stops_at_the_limit_and_on_an_empty_first_page() -> None:
    check, seen = site(50)
    assert search_last_page(check, 1, 20).last_page == 20
    assert seen[-1] == 20

    empty, _ = site(0)
    assert search_last_page(empty, 1, 20).status == "empty"


def test_a_block_stops_the_search_with_what_is_known() -> None:
    check, seen = site(40, stop={9: PageKind.BLOCKED})

    result = search_last_page(check, 1, 100)

    assert seen == [1, 2, 3, 5, 9]
    assert (result.status, result.last_page) == ("blocked", 5)
    assert result.stopped_by.page == 9


class Navigator:
    def __init__(self, *states: PageState, body: str = "") -> None:
        self.states = list(states)
        self.opened: list[str] = []
        self.page = type("Page", (), {"body_text": lambda _self: body})()

    def navigate(self, url) -> Navigation:
        self.opened.append(url)
        return Navigation(url, url, 200)

    def wait(self, page_type, allow_reload=True) -> PageOutcome:
        return self._next(self.opened[-1])

    def _next(self, url: str) -> PageOutcome:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return PageOutcome(state, PageType.LIST, url, reason=state.value)


class Pacer:
    def pause(self, seconds: float) -> None:
        pass


def checker(navigator: Navigator, **settings) -> PageChecker:
    options = {"probe_wait_seconds": 0, **settings}
    return PageChecker(
        navigator, lambda page: f"https://shop.test/c?page={page}", Discovery(**options), Pacer()
    )


def test_an_empty_page_is_confirmed_by_reopening_it() -> None:
    navigator = Navigator(PageState.EMPTY, PageState.READY)

    result = checker(navigator, empty_confirmations=2)(7)

    assert result.kind is PageKind.ITEMS
    assert navigator.opened == ["https://shop.test/c?page=7"] * 2


def test_unknown_pages_are_retried_then_read_by_recommendation_markers() -> None:
    navigator = Navigator(PageState.UNKNOWN, body="Возможно, вам понравится")

    plain = checker(Navigator(PageState.UNKNOWN), unknown_retries=1)(3)
    marked = checker(navigator, empty_when_no_items_text_markers=("возможно, вам понравится",))(3)

    assert plain.kind is PageKind.UNKNOWN
    assert (marked.kind, marked.reason) == (
        PageKind.EMPTY,
        "recommendations:возможно, вам понравится",
    )


def test_a_ready_page_without_items_and_a_404_count_as_empty() -> None:
    assert checker(Navigator(PageState.NOT_FOUND))(9).kind is PageKind.EMPTY


def package(tmp_path: Path, **changes) -> ScraperPackage:
    source = Path(__file__).parents[1] / "scrapers" / "controlled_catalog" / "scraper.json"
    definition = json.loads(source.read_text("utf-8"))
    definition["links"][0]["urls"] = [
        "https://shop.test/c/?sort=price",
        "https://shop.test/c/?sort=new",
        "https://shop.test/d/",
    ]
    for path, value in changes.items():
        target = definition
        *parents, last = path.split("__")
        for key in parents:
            target = target.setdefault(key, {})
        target[last] = value
    return ScraperPackage(tmp_path, ScraperDefinition.model_validate(definition), "v1")


def test_planner_puts_the_probe_first_and_discovers_shared_links_once(tmp_path: Path) -> None:
    catalog = package(
        tmp_path,
        discovery__enabled=True,
        health_check={"required_fields": ["item_name"]},
    )

    probe, *discover = plan_tasks("r1", catalog)

    assert (probe.kind, probe.payload, probe.priority) == (
        TaskKind.PROBE,
        {"links": ["https://shop.test/c/?sort=price"]},
        100,
    )
    assert [spec.kind for spec in discover] == [TaskKind.DISCOVER, TaskKind.DISCOVER]
    assert discover[0].payload["links"] == [
        "https://shop.test/c/?sort=price",
        "https://shop.test/c/?sort=new",
    ]
    assert page_count_key("https://a/c?page=2&sort=x&q=1") == page_count_key("https://a/c?q=1")


def test_planner_splits_a_known_page_range(tmp_path: Path) -> None:
    catalog = package(tmp_path, pages__list__pagination__last_page=5)

    specs = plan_tasks("r1", catalog, pages_per_task=2)

    assert [spec.payload["pages"] for spec in specs[:3]] == [[1, 2], [3, 4], [5, 5]]
    assert len(specs) == 9 and len({spec.task_id for spec in specs}) == 9
    # The package's own discover_last_page may search without an address template.
    without_template = package(
        tmp_path, discovery__enabled=True, pages__list__pagination__url_template=None
    )
    assert {spec.kind for spec in plan_tasks("r1", without_template)} == {TaskKind.DISCOVER}


def discover_task(links: list[str]) -> Task:
    return Task(
        task_id="r1:c:discover:00001",
        run_id="r1",
        scraper="c",
        kind=TaskKind.DISCOVER,
        payload={"link": links[0], "links": links},
        status=TaskStatus.RUNNING,
        attempts=3,
        max_attempts=3,
    )


def found(status: str, last: int | None, failed: bool = False) -> TaskOutcome:
    return TaskOutcome(
        AttemptStatus.FAILED if failed else AttemptStatus.COMPLETED,
        category=ErrorCategory.BLOCKED if failed else None,
        details={"status": status, "last_page": last, "reason": status},
    )


@pytest.mark.parametrize(
    ("discovery", "outcome", "pages"),
    [
        ({}, found("ok", 5), [[1, 2], [3, 4], [5, 5]]),
        ({}, found("empty", None), []),
        ({"on_blocked": "skip_link"}, found("blocked", 3, True), [[1, 2], [3, 3]]),
        ({"on_blocked": "skip_link"}, found("blocked", None, True), []),
        ({"on_blocked": "fallback_first_chunk"}, found("blocked", None, True), [[1, 2]]),
        ({"on_unknown": "fallback_first_chunk"}, found("unknown", None), [[1, 2]]),
        ({"on_unknown": "fallback_config_limit"}, found("unknown", 1), [[1, 2], [3, 3]]),
        ({"on_unknown": "skip_link"}, found("unknown", 1), [[1, 1]]),
    ],
)
def test_discovery_results_become_list_ranges(tmp_path, discovery, outcome, pages) -> None:
    catalog = package(tmp_path, discovery={"enabled": True, **discovery})

    followup = after_discovery(
        discover_task(["https://shop.test/c/"]), outcome, catalog, pages_per_task=2, max_attempts=3
    )

    assert [spec.payload.get("pages") for spec in followup.specs] == pages
    assert followup.error is None


def test_unknown_without_a_configured_limit_reads_the_whole_link(tmp_path) -> None:
    catalog = package(
        tmp_path,
        discovery={"enabled": True, "on_unknown": "fallback_config_limit"},
        pages__list__pagination__last_page=0,
    )

    followup = after_discovery(
        discover_task(["https://shop.test/c/"]),
        found("unknown", 1),
        catalog,
        pages_per_task=2,
        max_attempts=3,
    )

    assert [spec.payload for spec in followup.specs] == [{"link": "https://shop.test/c/"}]


def test_fail_run_policy_and_shared_links(tmp_path) -> None:
    catalog = package(tmp_path, discovery={"enabled": True, "on_blocked": "fail_run"})
    links = ["https://shop.test/c/?sort=a", "https://shop.test/c/?sort=b"]

    failed = after_discovery(
        discover_task(links), found("blocked", 2, True), catalog, pages_per_task=0, max_attempts=3
    )
    shared = after_discovery(
        discover_task(links), found("ok", 4), catalog, pages_per_task=0, max_attempts=3
    )

    assert failed.error and not failed.specs
    assert [(spec.payload["link"], spec.payload["pages"]) for spec in shared.specs] == [
        (links[0], [1, 4]),
        (links[1], [1, 4]),
    ]


def test_health_needs_items_with_required_fields() -> None:
    check = HealthCheck(min_items=2)
    items = [{"name": "a", "price": "1"}, {"name": "b", "price": ""}]

    assert evaluate(items, check, required_fields=("name",)).ok
    broken = evaluate(items, check, required_fields=("name", "price"))
    assert (broken.category, broken.missing) == (ErrorCategory.INVALID_CONTENT, ())
    assert broken.reason == "list_required_fields_incomplete"
    blocked = evaluate([], check, page_category=ErrorCategory.CAPTCHA)
    assert (blocked.ok, blocked.category) == (False, ErrorCategory.CAPTCHA)
    assert evaluate([], check).reason == "list_selectors_broken"


def test_gates_let_a_probing_scraper_take_only_its_probe() -> None:
    gates = Gates(["a", "b", "c"], probing={"b"})
    gates.close("c", "no proxies")

    assert gates.claimable(["a", "b", "c"]) == [(["b"], {TaskKind.PROBE}), (["a"], None)]
    gates.open("b", "probe passed")
    assert gates.claimable(["b"]) == [(["b"], None)]
    assert gates.summary()["c"] == {"state": "closed", "reason": "no proxies"}


def test_profile_state_remembers_preparation_for_a_while(tmp_path: Path) -> None:
    now = [1000.0]
    state = ProfileState(tmp_path, clock=lambda: now[0])
    state.mark("bootstrap", "v1")

    assert state.is_fresh("bootstrap", "v1", 60)
    assert not state.is_fresh("bootstrap", "v2", 60)
    assert not state.is_fresh("warmup", "v1", 60)
    now[0] += 61
    assert not state.is_fresh("bootstrap", "v1", 60)
    assert not ProfileState(tmp_path).is_fresh("bootstrap", "v1", 0)


def test_page_ranges_leave_out_saved_pages() -> None:
    assert page_ranges(1, 13, 3, {1, 2, 3, 5, 9}) == [(4, 4), (6, 8), (10, 12), (13, 13)]
    assert page_ranges(1, 3, 2, {1, 2, 3}) == []
    assert page_ranges(1, 5, 0) == [(1, 5)]


def test_saved_pages_are_skipped_for_the_probed_link_only(tmp_path) -> None:
    catalog = package(tmp_path, discovery={"enabled": True})
    links = ["https://shop.test/c/?sort=a", "https://shop.test/c/?sort=b"]

    followup = after_discovery(
        discover_task(links),
        found("ok", 3),
        catalog,
        pages_per_task=2,
        max_attempts=3,
        saved_pages=[1, 2, 3],
    )

    assert [(spec.payload["link"], spec.payload["pages"]) for spec in followup.specs] == [
        (links[1], [1, 2]),
        (links[1], [3, 3]),
    ]


def test_an_unknown_end_after_saved_pages_reads_on_from_the_last_one(tmp_path) -> None:
    catalog = package(
        tmp_path,
        discovery={"enabled": True, "on_unknown": "fallback_config_limit"},
        pages__list__pagination__last_page=0,
    )

    followup = after_discovery(
        discover_task(["https://shop.test/c/"]),
        found("unknown", 5),
        catalog,
        pages_per_task=2,
        max_attempts=3,
        saved_pages=[1, 2, 3, 5],
    )

    assert [spec.payload["pages"] for spec in followup.specs] == [[4, 4], [6, 0]]


def test_the_reader_keeps_items_and_rules_out_repeated_pages() -> None:
    calls = []

    def read(page: int):
        calls.append(page)
        return (PageKind.EMPTY, "repeats_page_3") if page == 4 else None

    navigator = Navigator(PageState.READY)
    check = PageChecker(
        navigator,
        lambda page: f"https://shop.test/c?page={page}",
        Discovery(),
        Pacer(),
        read_items=read,
    )

    assert check(3).kind is PageKind.ITEMS
    assert (check(4).kind, check(4).reason) == (PageKind.EMPTY, "repeats_page_3")
    assert calls == [3, 4, 4]
    assert checker(Navigator(PageState.EMPTY))(5).kind is PageKind.EMPTY

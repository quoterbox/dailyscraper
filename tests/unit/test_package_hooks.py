"""Hooks live packages rely on: page errors, collecting a link or an input row, warnings."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dailyscraper.actions import ErrorCategory, PageError, ScraperActions
from dailyscraper.detection.errors import classify_error
from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.execution.interventions import ManualIntervention
from dailyscraper.execution.navigator import Navigator as SessionNavigator
from dailyscraper.execution.sink import MemorySink
from dailyscraper.output import scraper_rows
from dailyscraper.pipelines import DefaultPipeline
from dailyscraper.schema import Inputs, ScraperDefinition, ScraperPackage
from dailyscraper.settings.models import BrowserSettings, ManualInterventionSettings
from dailyscraper.tasks.inputs import create_input_snapshot
from dailyscraper.tasks.planner import plan_tasks
from dailyscraper.transport.page import Navigation
from tests.unit.test_output import make_run


def definition(**extra: Any) -> ScraperDefinition:
    return ScraperDefinition.model_validate(
        {
            "schema_version": 3,
            "name": "shop",
            "website": "https://shop.test",
            "links": [{"id": "main", "urls": ["https://shop.test/c"]}],
            "pages": {
                "list": {
                    "templates": [
                        {
                            "name": "grid",
                            "first_item": {
                                "body": {"css": "li"},
                                "fields": {"name": {"css": ".name"}},
                            },
                        }
                    ],
                    "pagination": {"start_page": 2, "url_template": "{url}?page={page}"},
                }
            },
            **extra,
        }
    )


class Navigator:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def navigate(self, url: str) -> Navigation:
        self.log.append(f"open {url}")
        return Navigation(url, url, 200)

    def wait(self, page_type, allow_reload: bool = True) -> PageOutcome:
        return PageOutcome(PageState.READY, page_type, "https://shop.test/c", "ready")


class Pacer:
    def check_interrupted(self) -> None:
        pass

    def delay(self, bounds) -> None:
        pass

    def pause(self, seconds: float) -> None:
        pass


class Actions(ScraperActions):
    def __init__(self, log: list[str], rows: dict[str, Any] | None = None) -> None:
        super().__init__(None, None, {}, lambda: None)
        self.log = log
        self.rows = rows or {}

    def after_first_open(self, url: str) -> None:
        self.log.append("after_first_open")

    def collect_list(self, url, pages):
        self.log.append(f"collect {pages}")
        return [{"name": "a"}, {"name": "b"}]

    def collect_from_input(self, row):
        found = self.rows[row["article"]]
        if isinstance(found, Exception):
            raise found
        return found

    def run_warnings(self):
        return [{"code": "partial"}]


def pipeline(actions: Actions, spec: ScraperDefinition, log: list[str]) -> DefaultPipeline:
    ctx = SimpleNamespace(
        definition=spec,
        page=None,
        finder=None,
        navigator=Navigator(log),
        actions=actions,
        sink=MemorySink(),
        pacer=Pacer(),
        snapshots=None,
    )
    return DefaultPipeline(ctx)


def test_a_page_error_keeps_its_category() -> None:
    error = PageError(ErrorCategory.BLOCKED, "ozon_bad_page")

    assert classify_error(error) is ErrorCategory.BLOCKED
    assert str(error) == "blocked: ozon_bad_page"


def test_open_page_needs_a_running_session() -> None:
    with pytest.raises(RuntimeError, match="running session"):
        ScraperActions(None, None, {}, lambda: None).open_page("https://shop.test/")


def test_collect_list_runs_once_the_link_is_open_and_gets_the_range() -> None:
    log: list[str] = []
    spec = definition()
    run = pipeline(Actions(log), spec, log)

    task = run.run_link("https://shop.test/c", pages=(4, 6))
    run.run_link("https://shop.test/c", max_pages=1)

    run.run_link("https://shop.test/c")

    assert log == [
        "open https://shop.test/c?page=4",
        "after_first_open",
        "collect (4, 6)",
        "open https://shop.test/c?page=2",
        "after_first_open",
        "collect (2, 2)",
        "open https://shop.test/c?page=2",
        "after_first_open",
        "collect (2, 0)",
    ]
    assert task.list_result.stop_reason == "collected_by_hook"
    assert [row.item for row in run.ctx.sink.rows] == [{"name": "a"}, {"name": "b"}] * 3
    assert run.run_warnings() == [{"code": "partial"}]


def test_input_rows_without_a_detail_page_are_collected_by_the_package() -> None:
    log: list[str] = []
    actions = Actions(
        log,
        {
            "1": [{"article": "1", "size": "16"}, {"article": "1", "size": "17"}],
            "2": [],
            "3": PageError(ErrorCategory.BLOCKED, "api 498"),
        },
    )
    run = pipeline(actions, definition(), log)

    result = run.run_rows(
        [{"article": "1"}, {"article": "2"}, {"article": "3"}], positions=[4, 5, 6]
    )

    assert [detail.category for detail in result.detail_results] == [
        None,
        ErrorCategory.NOT_FOUND,
        ErrorCategory.BLOCKED,
    ]
    assert [(row.input_row, row.status, row.item.get("size")) for row in run.ctx.sink.rows] == [
        (4, None, "16"),
        (4, None, "17"),
        (5, "not_found", None),
        (6, "blocked", None),
    ]


def test_a_package_without_detail_page_or_collector_fails_clearly() -> None:
    run = pipeline(ScraperActions(None, None, {}, lambda: None), definition(), [])

    with pytest.raises(ValueError, match="collect_from_input"):
        run.run_rows([{"article": "1"}])


def test_every_item_of_an_input_row_is_kept_and_a_retry_replaces_them(tmp_path: Path) -> None:
    store, layout, sink = make_run(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text("article\n1\n2\n", encoding="utf-8")
    snapshot = create_input_snapshot(
        source, layout.inputs / "details.csv", Inputs(required_fields=("article",))
    )
    sink.emit({"article": "1", "size": "16"}, input_row=1, status="blocked")
    sink.emit({"article": "2", "size": "16"}, input_row=2)
    sink.emit({"article": "2", "size": "17"}, input_row=2, part=1)
    sink.emit({"article": "1", "size": "16"}, input_row=1)
    sink.emit({"article": "1", "size": "18"}, input_row=1, part=1)
    sink.close()

    rows = list(scraper_rows(store.partitions, layout, "r1", "details", snapshot))

    assert [(row["article"], row["size"]) for row in rows] == [
        ("1", "16"),
        ("1", "18"),
        ("2", "16"),
        ("2", "17"),
    ]


def test_input_rows_of_a_list_only_package_become_tasks(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    source.write_text("article\n1\n2\n3\n", encoding="utf-8")
    snapshot = create_input_snapshot(
        source, tmp_path / "snapshot.csv", Inputs(required_fields=("article",))
    )
    package = ScraperPackage(root=tmp_path, definition=definition(), version="v1")

    tasks = plan_tasks("r1", package, snapshot=snapshot, items_per_task=2)

    assert [task.payload["rows"] for task in tasks] == [[1, 2], [3]]


class BootstrapPage:
    url = "https://shop.test/geo/"

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.forgotten = 0

    def open(self, url: str) -> Navigation:
        self.opened.append(url)
        return Navigation(url, url, 200)

    def forget_navigation_status(self) -> None:
        self.forgotten += 1


class PickupActions(ScraperActions):
    def __init__(self, states: list[PageState], *, captcha_runs: int = 1) -> None:
        super().__init__(None, None, {}, lambda: None)
        self.states = states
        self.captcha_runs = captcha_runs
        self.runs = 0

    def bootstrap_version(self) -> str:
        return "pickup_v1"

    def bootstrap(self, url: str) -> bool:
        self.runs += 1
        if self.runs <= self.captcha_runs:
            raise PageError(ErrorCategory.CAPTCHA, "challenge on the geo page")
        return True

    def probe_bootstrap_state(self) -> PageOutcome:
        state = self.states.pop(0)
        return PageOutcome(state, PageType.LIST, BootstrapPage.url, state.value)


def navigator(actions: ScraperActions, page: BootstrapPage, *, timeout: float = 60):
    pacer = Pacer()
    clock = iter(range(0, 10_000, 5))
    return SessionNavigator(
        page=page,
        finder=None,
        definition=definition(),
        actions=actions,
        browser=BrowserSettings(),
        pacer=pacer,
        intervention=ManualIntervention(
            ManualInterventionSettings(timeout_seconds=timeout, poll_interval_seconds=5),
            headless=False,
            pacer=pacer,
            clock=lambda: next(clock),
        ),
    )


def test_a_challenge_during_bootstrap_waits_for_a_person_then_bootstrap_goes_on() -> None:
    page = BootstrapPage()
    actions = PickupActions([PageState.CAPTCHA, PageState.READY])

    navigation = navigator(actions, page).navigate("https://shop.test/c")

    assert navigation.ok
    assert actions.runs == 2
    assert page.forgotten == 1
    assert page.opened == ["https://shop.test/c", "https://shop.test/c"]


def test_an_unsolved_bootstrap_challenge_fails_and_is_tried_again_next_time() -> None:
    page = BootstrapPage()
    actions = PickupActions([PageState.CAPTCHA] * 100, captcha_runs=1)
    session = navigator(actions, page, timeout=10)

    with pytest.raises(PageError, match="captcha"):
        session.navigate("https://shop.test/c")
    assert session.navigate("https://shop.test/c").ok
    assert actions.runs == 2

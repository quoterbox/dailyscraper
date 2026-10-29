from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from dailyscraper.detection import category_for_state
from dailyscraper.domain import ErrorCategory, PageOutcome, PageType
from dailyscraper.execution.context import PageContext
from dailyscraper.execution.discovery import (
    PageChecker,
    PageKind,
    SearchResult,
    search_last_page,
)
from dailyscraper.execution.health import HealthReport, evaluate
from dailyscraper.page_scrapers import (
    AuthPageScraper,
    DetailPageScraper,
    DetailRowResult,
    ListPageScraper,
    ListResult,
    PageRange,
    RepeatDetector,
)
from dailyscraper.schema.scraper import HealthCheck

logger = logging.getLogger(__name__)

Row = dict[str, Any]


@dataclass
class LinkResult:
    link: str
    outcome: PageOutcome | None = None
    category: ErrorCategory | None = None
    list_result: ListResult | None = None
    detail_results: list[DetailRowResult] = field(default_factory=list)


@dataclass
class PipelineResult:
    links: list[LinkResult] = field(default_factory=list)
    detail_results: list[DetailRowResult] = field(default_factory=list)
    emitted: int = 0


class DefaultPipeline:
    """The standard scenario: ``auth? -> list -> detail`` for links, ``detail`` for input rows.

    A package customises it in ``pipeline.py`` with ``class Pipeline(DefaultPipeline)``.
    """

    def __init__(self, ctx: PageContext) -> None:
        self.ctx = ctx
        self._emitted = 0
        self._captured: list[Row] | None = None
        pages = ctx.definition.pages
        self.list = ListPageScraper(ctx, pages.list) if pages.list else None
        self.detail = DetailPageScraper(ctx, pages.detail) if pages.detail else None
        self.auth = AuthPageScraper(ctx, pages.auth) if pages.auth else None
        if pages.list is not None and pages.list.open_detail_by_click:
            raise NotImplementedError("open_detail_by_click is not supported yet")

    def run_links(self, links: list[str], *, pages: PageRange | None = None) -> PipelineResult:
        if self.list is None:
            raise ValueError(f"{self.ctx.definition.name} has no list page to start from links")
        actions = self.ctx.actions
        result = PipelineResult()
        actions.before_run()
        emitted_before = self._emitted
        for link in actions.filter_links(links):
            self.ctx.pacer.check_interrupted()
            result.links.append(self.run_link(link, pages=pages))
        actions.after_run()
        result.emitted = self._emitted - emitted_before
        return result

    def run_rows(
        self, rows: list[Row], *, positions: Sequence[int] | None = None
    ) -> PipelineResult:
        """Detail pages for input rows; each row is emitted with its 1-based input position.

        ``positions`` are the rows' positions in the whole input when ``rows`` is
        a planned part of it; then ``start_item``/``last_item`` are already applied.
        """
        if self.detail is None:
            raise ValueError(f"{self.ctx.definition.name} has no detail page for input rows")
        result = PipelineResult()
        self.ctx.actions.before_run()
        emitted_before = self._emitted
        result.detail_results = self.run_details(rows, input_positions=True, positions=positions)
        self.ctx.actions.after_run()
        result.emitted = self._emitted - emitted_before
        return result

    def run_link(
        self,
        link: str,
        *,
        pages: PageRange | None = None,
        max_pages: int = 0,
        detail_limit: int | None = None,
    ) -> LinkResult:
        actions = self.ctx.actions
        navigator = self.ctx.navigator
        result = LinkResult(link)
        emit_pages = self._emit if self.detail is None else None
        collected = actions.collect_list(link)
        if collected is not None:
            result.list_result = ListResult(items=list(collected), stop_reason="collected_by_hook")
            if emit_pages is not None:
                emit_pages(result.list_result.items)
        else:
            url = self.list.start_url(link, pages)
            navigation = navigator.navigate(url)
            if not navigation.ok:
                result.outcome = navigator.failed_open(url, PageType.LIST, navigation.error)
                result.category = category_for_state(result.outcome.state)
                self._snapshot("list", result.outcome)
                return result
            self._login(link)
            actions.after_first_open(link)
            result.outcome = navigator.wait(PageType.LIST)
            if not result.outcome.is_ready:
                result.category = category_for_state(
                    result.outcome.state, result.outcome.http_status
                )
                self._snapshot("list", result.outcome)
                return result
            actions.before_list(link)
            result.list_result = self.list.run(
                link, result.outcome, on_page=emit_pages, pages=pages, max_pages=max_pages
            )
        actions.after_list(result.list_result.items)
        if self.detail is not None:
            items = result.list_result.items
            result.detail_results = self.run_details(
                items if detail_limit is None else items[:detail_limit]
            )
        return result

    def probe(
        self,
        *,
        links: Sequence[str] = (),
        rows: list[Row] | None = None,
        positions: Sequence[int] | None = None,
    ) -> HealthReport:
        """A small run that saves nothing: the first catalog page, or a few detail rows."""
        check = self.ctx.definition.health_check or HealthCheck()
        with self.collect():
            if links:
                return self._probe_link(links[0], check)
            if rows is None:
                raise ValueError("a probe needs links or input rows")
            results = self.run_details(rows, input_positions=True, positions=positions)
            return self._detail_report(results, check)

    def discover(self, link: str) -> SearchResult:
        if self.list is None:
            raise ValueError(f"{self.ctx.definition.name} has no list page to discover")
        pagination = self.list.page.pagination
        start = pagination.start_page
        limit = pagination.last_page or self.ctx.definition.discovery.max_page
        own = self.ctx.actions.discover_last_page(link)
        if own is not None:
            if own < start:
                return SearchResult("empty", None, "found_by_hook")
            return SearchResult("ok", min(own, limit), "found_by_hook")
        if pagination.url_template is None:
            raise ValueError("discovery needs pagination.url_template")
        settings = self.ctx.definition.discovery
        saved: list[int] = []
        emitted_before = self._emitted
        rules = pagination.repeats
        if rules.match == "any":
            # Against every page read, one shared promoted item is no sign of the end.
            rules = rules.model_copy(update={"match": "all"})
        # Pages are visited out of order, so a repeat may be of any page read.
        repeats = RepeatDetector(rules, self.list.item_key, compare_with="seen")
        # Details of saved items would make discovery as long as the whole link.
        keep = settings.save_items and self.detail is None

        def read(page: int) -> tuple[PageKind, str] | None:
            items = self.list.read_page(link)
            if not items:
                return PageKind.UNKNOWN, "no_items_extracted"
            # Some shops answer a page past the end with the last real page.
            repeat = repeats.check(items)
            if repeat.repeated:
                return PageKind.EMPTY, repeat.reason
            repeats.remember(page, items)
            if keep:
                self._emit(items)
                saved.append(page)
            return None

        checker = PageChecker(
            self.ctx.navigator,
            lambda page: self.list.page_url(link, page),
            settings,
            self.ctx.pacer,
            read_items=read if keep or pagination.repeats.enabled else None,
        )
        result = search_last_page(checker, start, limit)
        return replace(
            result, saved_pages=tuple(sorted(saved)), rows=self._emitted - emitted_before
        )

    def _probe_link(self, link: str, check: HealthCheck) -> HealthReport:
        with_details = self.detail is not None and check.check_detail != "never"
        result = self.run_link(
            link, max_pages=1, detail_limit=check.min_items if with_details else 0
        )
        report = evaluate(
            result.list_result.items if result.list_result else [],
            check,
            required_fields=check.required_fields,
            page_category=result.category,
            page_ready=result.outcome is None or result.outcome.is_ready,
        )
        if not report.ok or not with_details:
            return report
        return self._detail_report(result.detail_results, check)

    def _detail_report(self, results: list[DetailRowResult], check: HealthCheck) -> HealthReport:
        found = [detail.row for detail in results if detail.category is None]
        failure = next((detail.category for detail in results if not detail.ok), None)
        return evaluate(
            found,
            check,
            required_fields=check.detail_required_fields,
            page_category=failure if not found else None,
            stage="detail",
        )

    def collect(self) -> _Capture:
        """Rows emitted inside the block are kept here instead of the sink (health probes)."""
        return _Capture(self)

    def run_details(
        self,
        rows: list[Row],
        *,
        input_positions: bool = False,
        positions: Sequence[int] | None = None,
    ) -> list[DetailRowResult]:
        page = self.detail.page
        results = []
        numbered = zip(positions, rows, strict=True) if positions is not None else None
        for position, row in numbered or enumerate(rows, start=1):
            if numbered is None:
                if position < page.start_item:
                    continue
                if page.last_item and position > page.last_item:
                    break
            self.ctx.pacer.check_interrupted()
            detail = self.detail.run_row(row)
            results.append(detail)
            if detail.category is not None and detail.category is not ErrorCategory.NOT_FOUND:
                self._snapshot("detail", detail.outcome)
            self._emit(
                [detail.row],
                input_row=position if input_positions else None,
                status=detail.category.value if detail.category else None,
            )
            self.ctx.pacer.delay(page.delays.between_items)
        return results

    def _login(self, link: str) -> None:
        if self.auth is None:
            return
        self.ctx.actions.before_login(link)
        if not self.auth.login():
            logger.error("Login failed for %s", link)
        self.ctx.actions.after_login()

    def _snapshot(self, kind: str, outcome: PageOutcome | None) -> None:
        if self.ctx.snapshots is not None:
            reason = outcome.reason if outcome is not None else "no outcome"
            self.ctx.snapshots.capture(self.ctx.page, f"{kind}-{reason}")

    def _emit(
        self, rows: list[Row], *, input_row: int | None = None, status: str | None = None
    ) -> None:
        for row in self.ctx.actions.prepare_output(rows):
            if self._captured is not None:
                self._captured.append(row)
                continue
            self.ctx.sink.emit(row, input_row=input_row, status=status)
            self._emitted += 1


class _Capture:
    def __init__(self, pipeline: DefaultPipeline) -> None:
        self.pipeline = pipeline
        self.rows: list[Row] = []

    def __enter__(self) -> list[Row]:
        self.pipeline._captured = self.rows
        return self.rows

    def __exit__(self, *exc: object) -> None:
        self.pipeline._captured = None

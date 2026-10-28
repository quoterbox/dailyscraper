from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dailyscraper.detection import category_for_state
from dailyscraper.domain import ErrorCategory, PageOutcome, PageType
from dailyscraper.execution.context import PageContext
from dailyscraper.page_scrapers import (
    AuthPageScraper,
    DetailPageScraper,
    DetailRowResult,
    ListPageScraper,
    ListResult,
)

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
        pages = ctx.definition.pages
        self.list = ListPageScraper(ctx, pages.list) if pages.list else None
        self.detail = DetailPageScraper(ctx, pages.detail) if pages.detail else None
        self.auth = AuthPageScraper(ctx, pages.auth) if pages.auth else None
        if pages.list is not None and pages.list.open_detail_by_click:
            raise NotImplementedError("open_detail_by_click is not supported yet")

    def run_links(self, links: list[str]) -> PipelineResult:
        if self.list is None:
            raise ValueError(f"{self.ctx.definition.name} has no list page to start from links")
        actions = self.ctx.actions
        result = PipelineResult()
        actions.before_run()
        emitted_before = self._emitted
        for link in actions.filter_links(links):
            self.ctx.pacer.check_interrupted()
            result.links.append(self.run_link(link))
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

    def run_link(self, link: str) -> LinkResult:
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
            url = self.list.start_url(link)
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
            result.list_result = self.list.run(link, result.outcome, on_page=emit_pages)
        actions.after_list(result.list_result.items)
        if self.detail is not None:
            result.detail_results = self.run_details(result.list_result.items)
        return result

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
            self.ctx.sink.emit(row, input_row=input_row, status=status)
            self._emitted += 1

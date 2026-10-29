"""A pipeline without a browser for slot and dispatcher tests.

Slots import it by path (``tests.fakes:open_fake``). Its behaviour comes from
``options.fake`` of the test package, optionally per attempt number::

    {"rows": 3, "attempts": {"1": {"crash_after": 2}}}

Keys: ``rows``, ``category`` (link failure), ``raise``, ``crash_after`` (exit
the process after so many committed rows), ``hang`` (stop reporting),
``row_delay`` (cancellable pause per row), ``statuses`` (detail status per
input position), ``captcha`` (a list page shows a challenge that a person solves
after ``solve_after`` checks, or never when it is ``null``). ``proxies`` overrides
the behaviour for a proxy id.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from dailyscraper.domain import ErrorCategory, PageOutcome, PageState, PageType
from dailyscraper.execution.interventions import ManualIntervention
from dailyscraper.execution.timing import Pacer
from dailyscraper.page_scrapers import DetailRowResult, ListResult
from dailyscraper.pipelines import LinkResult, PipelineResult

OPENER = "tests.fakes:open_fake"


@contextmanager
def open_fake(
    package, settings, sink, pacer=None, snapshots=None, plan=None, interventions=None
) -> Iterator[FakePipeline]:
    pacer = pacer or Pacer()
    intervention = ManualIntervention(
        settings.interventions.manual, headless=False, pacer=pacer, reporter=interventions
    )
    yield FakePipeline(package.definition.options.get("fake", {}), sink, pacer, plan, intervention)


class FakePipeline:
    def __init__(
        self, options: dict[str, Any], sink, pacer: Pacer, plan=None, intervention=None
    ) -> None:
        self.options = options
        self.sink = sink
        self.pacer = pacer
        self.proxy_id = plan.proxy.proxy_id if plan and plan.proxy else "direct"
        self.intervention = intervention

    def behaviour(self) -> dict[str, Any]:
        attempt = (self.sink.attempt_id or "#1").rpartition("#")[2]
        base = {
            key: value for key, value in self.options.items() if key not in {"attempts", "proxies"}
        }
        return {
            **base,
            **self.options.get("attempts", {}).get(attempt, {}),
            **self.options.get("proxies", {}).get(self.proxy_id, {}),
        }

    def run_links(self, links: list[str]) -> PipelineResult:
        behaviour = self.behaviour()
        (link,) = links
        if "captcha" in behaviour and not self._solved(link, behaviour["captcha"]):
            return PipelineResult(links=[LinkResult(link, category=ErrorCategory.CAPTCHA)])
        items = []
        for number in range(behaviour.get("rows", 3)):
            item = {
                "item_name": f"{link}#{number}",
                "detail_link": f"{link}/p{number}",
                "proxy": self.proxy_id,
            }
            items.append(item)
            self._row(behaviour, item)
        self._end(behaviour)
        category = behaviour.get("category")
        return PipelineResult(
            links=[
                LinkResult(
                    link,
                    category=ErrorCategory(category) if category else None,
                    list_result=ListResult(items=items, stop_reason="last_page"),
                )
            ],
            emitted=len(items),
        )

    def run_rows(self, rows: list[dict[str, Any]], *, positions=None) -> PipelineResult:
        behaviour = self.behaviour()
        statuses = behaviour.get("statuses", {})
        results = []
        for position, row in zip(positions or range(1, len(rows) + 1), rows, strict=True):
            status = statuses.get(str(position))
            item = {**row, "title": f"title {position}"}
            self._row(behaviour, item, input_row=position, status=status)
            results.append(DetailRowResult(item, None, ErrorCategory(status) if status else None))
        self._end(behaviour)
        return PipelineResult(detail_results=results, emitted=len(results))

    def _solved(self, link: str, captcha: dict[str, Any]) -> bool:
        solve_after = captcha.get("solve_after")
        checks = 0

        def probe() -> PageOutcome:
            nonlocal checks
            checks += 1
            solved = solve_after is not None and checks >= solve_after
            state = PageState.READY if solved else PageState.CAPTCHA
            return PageOutcome(state, PageType.LIST, link, reason=state.value)

        challenge = PageOutcome(PageState.CAPTCHA, PageType.LIST, link, reason="captcha_marker")
        return self.intervention(challenge, probe).state is PageState.READY

    def _row(self, behaviour: dict[str, Any], item, **kwargs) -> None:
        self.pacer.check_interrupted()
        if behaviour.get("row_delay"):
            self.pacer.pause(behaviour["row_delay"])
        self.sink.emit(item, **kwargs)
        crash_after = behaviour.get("crash_after")
        if crash_after is not None and self.sink.count >= crash_after:
            self.sink.flush()
            os._exit(3)

    def _end(self, behaviour: dict[str, Any]) -> None:
        if behaviour.get("hang"):
            while True:
                time.sleep(0.2)
        if behaviour.get("raise"):
            raise RuntimeError(behaviour["raise"])

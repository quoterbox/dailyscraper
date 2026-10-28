from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dailyscraper.domain import PageOutcome, PageType
from dailyscraper.execution.context import PageContext
from dailyscraper.page_scrapers.extraction import is_empty_value, read_value
from dailyscraper.pages import CompiledListTemplate, ListPage, compile_list_template
from dailyscraper.transport import Element

logger = logging.getLogger(__name__)

Item = dict[str, Any]


@dataclass(frozen=True)
class ListPageVisit:
    page_num: int
    url: str
    items_found: int
    outcome: PageOutcome


@dataclass
class ListResult:
    items: list[Item] = field(default_factory=list)
    visits: list[ListPageVisit] = field(default_factory=list)
    stop_reason: str = ""
    last_outcome: PageOutcome | None = None


class ListPageScraper:
    def __init__(self, ctx: PageContext, page: ListPage) -> None:
        self.ctx = ctx
        self.page = page
        self.templates = [compile_list_template(template) for template in page.templates]
        self._position = 0
        self._on_page: Callable[[list[Item]], None] | None = None

    def start_url(self, link: str) -> str:
        pagination = self.page.pagination
        if pagination.url_template is None:
            return link
        return pagination.url_template.format(url=link, page=pagination.start_page)

    def run(
        self,
        link: str,
        first_outcome: PageOutcome,
        *,
        on_page: Callable[[list[Item]], None] | None = None,
    ) -> ListResult:
        """``on_page`` receives each page's accepted items as soon as the page is read."""
        result = ListResult(last_outcome=first_outcome)
        self._position = 0
        self._on_page = on_page
        if self.page.pagination.infinity_loading:
            self._run_infinite(link, first_outcome, result)
        else:
            self._run_paged(link, first_outcome, result)
        logger.info(
            "List finished: link=%s items=%s pages=%s reason=%s",
            link,
            len(result.items),
            len(result.visits),
            result.stop_reason,
        )
        return result

    def _run_paged(self, link: str, outcome: PageOutcome, result: ListResult) -> None:
        pagination = self.page.pagination
        page_num = pagination.start_page if pagination.url_template else 1
        previous: list[Item] = []
        same_content = 0
        while True:
            result.last_outcome = outcome
            if not outcome.is_ready:
                result.stop_reason = f"page_state:{outcome.state.value}"
                return
            template = self._loaded_template()
            if template is None:
                result.stop_reason = "template_not_found"
                return
            if page_num >= pagination.start_page:
                page_items = self._extract(
                    self.ctx.finder.find_all(template.items.item, self.page.waits.item), template
                )
                if (
                    pagination.wait_for_changing_content
                    and previous
                    and _same_items(previous, page_items, pagination.stop_if_all_items_same)
                ):
                    same_content += 1
                    logger.warning("Page %s repeats the previous page (%s)", page_num, same_content)
                    if same_content >= pagination.max_same_content_pages:
                        result.stop_reason = "same_content"
                        return
                    self.ctx.pacer.delay(self.page.delays.before_next_page)
                    outcome = self.ctx.navigator.wait(PageType.LIST, allow_reload=False)
                    continue
                same_content = 0
                previous = page_items
                result.visits.append(ListPageVisit(page_num, outcome.url, len(page_items), outcome))
                if not self._accept(page_items, link, result):
                    result.stop_reason = "last_item"
                    return
            if pagination.last_page and page_num >= pagination.last_page:
                result.stop_reason = "last_page"
                return
            self.ctx.pacer.delay(self.page.delays.before_next_page)
            next_outcome = self._open_page(template, link, page_num + 1)
            if next_outcome is None:
                result.stop_reason = "no_next_page"
                return
            page_num += 1
            outcome = next_outcome

    def _run_infinite(self, link: str, outcome: PageOutcome, result: ListResult) -> None:
        if not outcome.is_ready:
            result.stop_reason = f"page_state:{outcome.state.value}"
            return
        template = self._loaded_template()
        if template is None:
            result.stop_reason = "template_not_found"
            return
        processed = 0
        same_content = 0
        while True:
            elements = self.ctx.finder.find_all(template.items.item, self.page.waits.item)
            new_items = self._extract(elements[processed:], template)
            if elements[processed:]:
                processed = len(elements)
                same_content = 0
                result.visits.append(
                    ListPageVisit(len(result.visits) + 1, outcome.url, len(new_items), outcome)
                )
                if not self._accept(new_items, link, result):
                    result.stop_reason = "last_item"
                    return
            else:
                same_content += 1
                if same_content >= self.page.pagination.max_same_content_pages:
                    result.stop_reason = "no_more_items"
                    return
            if template.template.next_page is not None:
                button = self.ctx.finder.find(
                    template.template.next_page, self.page.waits.next_page
                )
                if button is None and not elements[processed:]:
                    result.stop_reason = "no_more_button"
                    return
                if button is not None:
                    button.scroll_into_view()
                    button.click()
            else:
                self.ctx.page.scroll_to_bottom()
            self.ctx.pacer.delay(self.page.delays.before_next_page)

    def _open_page(
        self, template: CompiledListTemplate, link: str, page_num: int
    ) -> PageOutcome | None:
        pagination = self.page.pagination
        if pagination.url_template is not None:
            url = pagination.url_template.format(url=link, page=page_num)
            return self.ctx.navigator.open(url, PageType.LIST, allow_reload=False)
        if template.template.next_page is None:
            return None
        button = self.ctx.finder.find(template.template.next_page, self.page.waits.next_page)
        if button is None:
            return None
        button.scroll_into_view()
        button.click()
        return self.ctx.navigator.wait(PageType.LIST, allow_reload=False)

    def _loaded_template(self) -> CompiledListTemplate | None:
        finder = self.ctx.finder
        for template in self.templates:
            ready = template.template.ready
            if ready is not None:
                if finder.find(ready, self.page.waits.item_body) is not None:
                    return template
            elif finder.count(template.items.item) > 0:
                return template
        return None

    def _extract(self, elements: list[Element], template: CompiledListTemplate) -> list[Item]:
        items = []
        base_url = self.ctx.page.url
        for element in elements:
            self.ctx.pacer.check_interrupted()
            if self.page.scroll.enabled:
                self._scroll_to(element)
            item = {
                name: self._field(element, name, selector, base_url)
                for name, selector in template.items.fields.items()
            }
            if not all(is_empty_value(value) for value in item.values()):
                items.append(item)
        return items

    def _field(self, element: Element, name: str, selector, base_url: str) -> str | None:
        found = element.find(selector, self.page.waits.field)
        if found is None:
            return None
        return read_value(
            found, selector, base_url=base_url, link=name == self.page.detail_link_field
        )

    def _accept(self, page_items: list[Item], link: str, result: ListResult) -> bool:
        accepted: list[Item] = []
        complete = True
        for item in page_items:
            self._position += 1
            if self._position < self.page.start_item:
                continue
            if self.page.last_item and self._position > self.page.last_item:
                complete = False
                break
            prepared = self.ctx.actions.prepare_list_item(item, link)
            if prepared is None:
                continue
            if self.page.save_start_link:
                prepared["start_link"] = link
            accepted.append(prepared)
            self.ctx.pacer.delay(self.page.delays.between_items)
        result.items.extend(accepted)
        if accepted and self._on_page is not None:
            self._on_page(accepted)
        return complete

    def _scroll_to(self, element: Element) -> None:
        scroll = self.page.scroll
        element.scroll_into_view()
        self.ctx.page.scroll_by(random.randint(*scroll.delta_x), random.randint(*scroll.delta_y))


def _same_items(previous: list[Item], current: list[Item], only_if_all_same: bool) -> bool:
    if not previous or not current:
        return False
    different = sum(1 for item in current if item not in previous)
    if different == 0:
        return True
    return different < len(current) and not only_if_all_same

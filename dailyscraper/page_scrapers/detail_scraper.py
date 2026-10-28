from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from dailyscraper.detection import category_for_state
from dailyscraper.domain import ErrorCategory, PageOutcome, PageState, PageType
from dailyscraper.execution.context import PageContext
from dailyscraper.page_scrapers.extraction import clean_text, is_empty_value, read_value
from dailyscraper.pages import (
    CompiledDetailTemplate,
    CompiledPropertyGroup,
    DetailPage,
    Selector,
    compile_detail_template,
    property_output_field,
)
from dailyscraper.pages.detail_page import PROPERTY_NAME_FIELD, PROPERTY_VALUE_FIELD
from dailyscraper.pages.xpath import is_relative
from dailyscraper.transport import Element

logger = logging.getLogger(__name__)

Row = dict[str, Any]


@dataclass(frozen=True)
class DetailRowResult:
    row: Row
    outcome: PageOutcome | None
    category: ErrorCategory | None
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.category is None or self.category is ErrorCategory.NOT_FOUND


class DetailPageScraper:
    def __init__(self, ctx: PageContext, page: DetailPage) -> None:
        self.ctx = ctx
        self.page = page
        self.templates = [compile_detail_template(template) for template in page.templates]

    def run_row(self, row: Row) -> DetailRowResult:
        link = row.get(self.page.link_field)
        if is_empty_value(link):
            logger.warning("Row has no %s, detail page skipped", self.page.link_field)
            return DetailRowResult(dict(row), None, ErrorCategory.INVALID_CONTENT)

        self.ctx.actions.before_detail(row)
        outcome = self.ctx.navigator.open(str(link), PageType.DETAIL)
        if outcome.state is PageState.NOT_FOUND:
            logger.info("Detail page not found: %s (%s)", link, outcome.reason)
            return DetailRowResult(dict(row), outcome, ErrorCategory.NOT_FOUND)
        if not outcome.is_ready:
            return DetailRowResult(
                dict(row), outcome, category_for_state(outcome.state, outcome.http_status)
            )

        template = self._loaded_template()
        enriched = self._extract(row, template)
        missing = self._missing(enriched, template)
        if missing:
            logger.warning("Retrying incomplete detail extraction: %s %s", link, missing)
            self.ctx.page.wait(self.page.waits.field * 1000)
            enriched = self._extract(row, template)
            missing = self._missing(enriched, template)
        self.ctx.actions.after_detail(enriched)
        category = ErrorCategory.INVALID_CONTENT if missing else None
        return DetailRowResult(enriched, outcome, category, tuple(missing))

    def _loaded_template(self) -> CompiledDetailTemplate:
        for template in self.templates:
            ready = template.template.ready
            if ready is None or self.ctx.finder.find(ready, 0) is not None:
                return template
        return self.templates[0]

    def _extract(self, row: Row, template: CompiledDetailTemplate) -> Row:
        enriched = dict(row)
        base_url = self.ctx.page.url
        for name, selector in template.template.fields.items():
            element = self.ctx.finder.find(selector, self.page.waits.field)
            enriched[name] = (
                read_value(element, selector, base_url=base_url) if element is not None else None
            )
            if element is not None and self.page.scroll.enabled:
                element.scroll_into_view()
        for group_name, group in template.property_groups.items():
            enriched.update(self._properties(group_name, group, base_url))
        return enriched

    def _properties(self, group_name: str, compiled: CompiledPropertyGroup, base_url: str) -> Row:
        group = compiled.group
        values: Row = {property_output_field(group_name, name): None for name in group.properties}
        rows = self._rows(compiled)
        if not rows:
            logger.warning("No property rows found for group %s", group_name)
            return values
        name_selector = compiled.rows.fields[PROPERTY_NAME_FIELD]
        value_selector = compiled.rows.fields[PROPERTY_VALUE_FIELD]
        cells = [(row.find(name_selector), row.find(value_selector)) for row in rows]
        for name, spec in group.properties.items():
            for name_cell, value_cell in cells:
                if name_cell is None or value_cell is None or spec.label not in name_cell.text():
                    continue
                raw = value_cell.attribute(spec.attribute) if spec.attribute else value_cell.text()
                if raw is not None and spec.prepare is not None:
                    raw = spec.prepare.apply(raw)
                if raw is not None and clean_text(raw):
                    values[property_output_field(group_name, name)] = clean_text(raw)
                    break
        return values

    def _rows(self, compiled: CompiledPropertyGroup) -> list[Element]:
        wait = self.page.waits.property
        item = compiled.rows.item
        body = compiled.group.body
        if body is None or _has_absolute_xpath(item):
            return self.ctx.finder.find_all(item, wait)
        scope = self.ctx.finder.find(body, wait)
        return scope.find_all(item, wait) if scope is not None else []

    def _missing(self, row: Row, template: CompiledDetailTemplate) -> list[str]:
        missing = [
            name for name in template.template.required_fields if is_empty_value(row.get(name))
        ]
        for group_name, compiled in template.property_groups.items():
            group = compiled.group
            if group.required and all(
                is_empty_value(row.get(property_output_field(group_name, name)))
                for name in group.properties
            ):
                missing.append(f"property_group:{group_name}")
        return missing


def _has_absolute_xpath(selector: Selector) -> bool:
    return any(not is_relative(xpath) for xpath in selector.xpaths)

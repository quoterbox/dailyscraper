from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from dailyscraper.pages.common import ItemSample
from dailyscraper.pages.detail_page import DetailTemplate, PropertyGroup
from dailyscraper.pages.list_page import ListTemplate
from dailyscraper.pages.selectors import Locator, Selector, SelectorKind
from dailyscraper.pages.xpath import XPathError, derive_item_xpath, is_relative, relative_to_item


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledItems:
    """``item`` matches every item element; ``fields`` are relative to one item."""

    item: Selector
    fields: Mapping[str, Selector]


@dataclass(frozen=True)
class CompiledListTemplate:
    template: ListTemplate
    items: CompiledItems


@dataclass(frozen=True)
class CompiledPropertyGroup:
    group: PropertyGroup
    rows: CompiledItems


@dataclass(frozen=True)
class CompiledDetailTemplate:
    template: DetailTemplate
    property_groups: Mapping[str, CompiledPropertyGroup]


def compile_list_template(template: ListTemplate) -> CompiledListTemplate:
    try:
        items = compile_items(template.item, template.first_item, template.second_item)
    except (TemplateError, XPathError) as error:
        raise TemplateError(f"list template {template.name!r}: {error}") from error
    return CompiledListTemplate(template=template, items=items)


def compile_detail_template(template: DetailTemplate) -> CompiledDetailTemplate:
    groups = {}
    for name, group in template.property_groups.items():
        try:
            rows = compile_items(group.item, group.first_item, group.second_item)
        except (TemplateError, XPathError) as error:
            raise TemplateError(
                f"detail template {template.name!r}, property group {name!r}: {error}"
            ) from error
        groups[name] = CompiledPropertyGroup(group=group, rows=rows)
    return CompiledDetailTemplate(template=template, property_groups=MappingProxyType(groups))


def compile_items(
    explicit_item: Selector | None,
    first: ItemSample,
    second: ItemSample | None,
) -> CompiledItems:
    collection = _derive_collection(first, second)
    if explicit_item is not None:
        item = explicit_item
        collection = collection or _single_absolute_xpath(explicit_item)
    elif collection is not None:
        item = Selector(locators=(Locator(kind=SelectorKind.XPATH, value=collection),))
    elif first.body is not None and first.body.css:
        item = first.body.model_copy(update={"locators": _css_only(first.body)})
    else:
        raise TemplateError(
            "cannot determine the item selector: set 'item', a CSS item body, "
            "or absolute XPaths for the first and second item"
        )
    fields = {name: _relative(selector, collection) for name, selector in first.fields.items()}
    return CompiledItems(item=item, fields=MappingProxyType(fields))


def _derive_collection(first: ItemSample, second: ItemSample | None) -> str | None:
    if second is None:
        return None
    pairs = []
    if first.body is not None and second.body is not None:
        pairs.append((first.body, second.body))
    for name, selector in first.fields.items():
        other = second.fields.get(name)
        if other is not None:
            pairs.append((selector, other))
    for left, right in pairs:
        left_xpath = _absolute_xpath(left)
        right_xpath = _absolute_xpath(right)
        if left_xpath and right_xpath:
            return derive_item_xpath(left_xpath, right_xpath)
    return None


def _absolute_xpath(selector: Selector) -> str | None:
    return next((xpath for xpath in selector.xpaths if not is_relative(xpath)), None)


def _single_absolute_xpath(selector: Selector) -> str | None:
    xpaths = selector.xpaths
    return xpaths[0] if len(xpaths) == 1 and not is_relative(xpaths[0]) else None


def _css_only(selector: Selector) -> tuple[Locator, ...]:
    return tuple(loc for loc in selector.locators if loc.kind is SelectorKind.CSS)


def _relative(selector: Selector, collection: str | None) -> Selector:
    locators = []
    for locator in selector.locators:
        if locator.kind is SelectorKind.XPATH and not is_relative(locator.value):
            if collection is None:
                raise TemplateError(
                    f"absolute XPath {locator.value!r} needs a second item sample "
                    "or an XPath item selector to be made relative to the item"
                )
            locator = Locator(
                kind=SelectorKind.XPATH, value=relative_to_item(collection, locator.value)
            )
        locators.append(locator)
    return selector.model_copy(update={"locators": tuple(locators)})

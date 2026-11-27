# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Declarative page models: Page, ListPage, DetailPage, AuthPage."""

from dailyscraper.pages.common import ItemSample, Scroll
from dailyscraper.pages.compiled import (
    CompiledDetailTemplate,
    CompiledItems,
    CompiledListTemplate,
    CompiledPropertyGroup,
    TemplateError,
    compile_detail_template,
    compile_list_template,
)
from dailyscraper.pages.kinds.auth import AuthPage, AuthTemplate, Credentials
from dailyscraper.pages.kinds.detail import (
    DetailPage,
    DetailTemplate,
    PropertyGroup,
    PropertySpec,
    property_output_field,
)
from dailyscraper.pages.kinds.list import ListPage, ListTemplate, Pagination, Repeats
from dailyscraper.pages.selectors import Locator, PrepareMode, Selector, SelectorKind, ValuePrepare
from dailyscraper.pages.state import PageStateRules
from dailyscraper.pages.xpath import XPathError, derive_item_xpath, relative_to_item

__all__ = [
    "AuthPage",
    "AuthTemplate",
    "CompiledDetailTemplate",
    "CompiledItems",
    "CompiledListTemplate",
    "CompiledPropertyGroup",
    "Credentials",
    "DetailPage",
    "DetailTemplate",
    "ItemSample",
    "ListPage",
    "ListTemplate",
    "Locator",
    "PageStateRules",
    "Pagination",
    "PrepareMode",
    "PropertyGroup",
    "PropertySpec",
    "Repeats",
    "Scroll",
    "Selector",
    "SelectorKind",
    "TemplateError",
    "ValuePrepare",
    "XPathError",
    "compile_detail_template",
    "compile_list_template",
    "derive_item_xpath",
    "property_output_field",
    "relative_to_item",
]

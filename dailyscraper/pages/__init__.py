"""Declarative page models: Page, ListPage, DetailPage, AuthPage."""

from dailyscraper.pages.auth_page import AuthPage, AuthTemplate, Credentials
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
from dailyscraper.pages.detail_page import (
    DetailPage,
    DetailTemplate,
    PropertyGroup,
    PropertySpec,
    property_output_field,
)
from dailyscraper.pages.list_page import ListPage, ListTemplate, Pagination
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

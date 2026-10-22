from __future__ import annotations

import pytest
from pydantic import ValidationError

from dailyscraper.pages import (
    DetailTemplate,
    ListTemplate,
    PageStateRules,
    PrepareMode,
    Selector,
    SelectorKind,
    TemplateError,
    ValuePrepare,
    compile_detail_template,
    compile_list_template,
)


def locators(selector: Selector) -> list[tuple[str, str]]:
    return [(locator.kind.value, locator.value) for locator in selector.locators]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"css": ".price"}, [("css", ".price")]),
        ({"xpath": ["//h1", "//h2"]}, [("xpath", "//h1"), ("xpath", "//h2")]),
        (
            {"selectors": [{"xpath": "//h1"}, {"css": "h1.title"}]},
            [("xpath", "//h1"), ("css", "h1.title")],
        ),
    ],
)
def test_selector_alternatives_keep_their_order(data, expected) -> None:
    assert locators(Selector.model_validate(data)) == expected


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"css": ".a", "xpath": "//a"}, "both 'css' and 'xpath'"),
        ({"css": ".a", "selectors": [{"xpath": "//a"}]}, "either 'selectors'"),
        ({"selectors": [{"css": ".a", "xpath": "//a"}]}, "each entry of 'selectors'"),
        ({"selectors": []}, "at least 1"),
        ({"css": ".a", "WAIT": 1}, "Extra inputs"),
        ({"css": ""}, "at least 1 character"),
    ],
)
def test_ambiguous_or_unknown_selector_input_is_rejected(data, message) -> None:
    with pytest.raises(ValidationError, match=message):
        Selector.model_validate(data)


@pytest.mark.parametrize(
    ("prepare", "value", "expected"),
    [
        ({"regex": r"\d+"}, "price 1 299 rub", "1"),
        ({"regex": r"\d+"}, "no digits", None),
        ({"regex": r"\s", "mode": "remove"}, "1 299", "1299"),
        ({"regex": "rub", "mode": "replace", "replace_with": "RUB"}, "10 rub", "10 RUB"),
    ],
)
def test_value_prepare_modes(prepare, value, expected) -> None:
    assert ValuePrepare.model_validate(prepare).apply(value) == expected


def test_value_prepare_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError):
        ValuePrepare.model_validate({"regex": "(unclosed"})
    assert ValuePrepare.model_validate({"regex": "a"}).mode is PrepareMode.FIND


def test_list_template_items_derived_from_item_body_samples() -> None:
    template = ListTemplate.model_validate(
        {
            "name": "catalog",
            "first_item": {
                "body": {"xpath": "/html/body/ul/li[1]"},
                "fields": {
                    "name": {"xpath": "/html/body/ul/li[1]/a/h2"},
                    "price": {"css": ".price"},
                },
            },
            "second_item": {"body": {"xpath": "/html/body/ul/li[2]"}},
        }
    )

    items = compile_list_template(template).items

    assert locators(items.item) == [("xpath", "/html/body/ul/li")]
    assert locators(items.fields["name"]) == [("xpath", "./a/h2")]
    assert locators(items.fields["price"]) == [("css", ".price")]
    assert template.first_item.fields["name"].xpaths == ("/html/body/ul/li[1]/a/h2",)


def test_list_template_items_derived_from_field_samples() -> None:
    template = ListTemplate.model_validate(
        {
            "name": "catalog",
            "first_item": {"fields": {"name": {"xpath": "/html/ul/li[1]/span"}}},
            "second_item": {"fields": {"name": {"xpath": "/html/ul/li[2]/span"}}},
        }
    )

    items = compile_list_template(template).items

    assert locators(items.item) == [("xpath", "/html/ul/li")]
    assert locators(items.fields["name"]) == [("xpath", "./span")]


def test_explicit_item_xpath_makes_absolute_fields_relative() -> None:
    template = ListTemplate.model_validate(
        {
            "name": "catalog",
            "item": {"xpath": "//div[@id='grid']/div"},
            "first_item": {"fields": {"link": {"xpath": "//div[@id='grid']/div[1]/a"}}},
        }
    )

    items = compile_list_template(template).items

    assert locators(items.fields["link"]) == [("xpath", "./a")]


def test_css_item_body_selects_all_items() -> None:
    template = ListTemplate.model_validate(
        {
            "name": "catalog",
            "first_item": {
                "body": {"css": "ul.products > li.product"},
                "fields": {"name": {"css": ".title"}},
            },
        }
    )

    assert locators(compile_list_template(template).items.item) == [
        ("css", "ul.products > li.product")
    ]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"name": "t", "first_item": {"fields": {"name": {"css": ".title"}}}},
            "cannot determine the item selector",
        ),
        (
            {
                "name": "t",
                "item": {"css": "li"},
                "first_item": {"fields": {"name": {"xpath": "/html/ul/li[1]/a"}}},
            },
            "needs a second item sample",
        ),
    ],
)
def test_template_compilation_errors_name_the_template(data, message) -> None:
    with pytest.raises(TemplateError, match=rf"list template 't': .*{message}"):
        compile_list_template(ListTemplate.model_validate(data))


def test_detail_property_group_rows_and_required_fields() -> None:
    template = DetailTemplate.model_validate(
        {
            "name": "detail",
            "fields": {"title": {"css": "h1"}},
            "required_fields": ["title"],
            "property_groups": {
                "attributes": {
                    "body": {"css": "table tbody"},
                    "first_item": {
                        "body": {"css": "tr"},
                        "fields": {"name": {"css": "th"}, "value": {"css": "td"}},
                    },
                    "properties": {"country": "country", "brand": {"label": "brand name"}},
                    "required": True,
                }
            },
        }
    )

    group = compile_detail_template(template).property_groups["attributes"]

    assert locators(group.rows.item) == [("css", "tr")]
    assert group.group.properties["country"].label == "country"
    assert group.group.required is True


def test_detail_template_rejects_unknown_required_field() -> None:
    with pytest.raises(ValidationError, match="required_fields are not defined"):
        DetailTemplate.model_validate(
            {"name": "detail", "fields": {"title": {"css": "h1"}}, "required_fields": ["price"]}
        )


def test_property_group_needs_name_and_value_cells() -> None:
    with pytest.raises(ValidationError, match=r"must define \['value'\]"):
        DetailTemplate.model_validate(
            {
                "name": "detail",
                "property_groups": {
                    "attributes": {
                        "first_item": {"fields": {"name": {"css": "th"}}},
                        "properties": {"country": "country"},
                    }
                },
            }
        )


def test_page_state_rules_specific_overrides_common() -> None:
    common = PageStateRules.model_validate(
        {"wait_attempts": 3, "empty_text_markers": ["nothing found"]}
    )
    specific = PageStateRules.model_validate(
        {"wait_attempts": 1, "ready_selectors": [{"css": "h1"}]}
    )

    merged = common.overridden_by(specific)

    assert merged.wait_attempts == 1
    assert merged.empty_text_markers == ("nothing found",)
    assert merged.ready_selectors[0].locators[0].kind is SelectorKind.CSS
    assert common.overridden_by(None) is common

from __future__ import annotations

from pathlib import Path

import pytest

from dailyscraper.schema import ScraperDefinition
from tools.convert_scraper import UnsupportedFormatError, convert, convert_package


@pytest.mark.parametrize("name", ["controlled_catalog", "controlled_details"])
def test_controlled_packages_are_reproduced_from_the_previous_format(
    fixtures_root: Path, project_root: Path, tmp_path: Path, name: str
) -> None:
    warnings = convert_package(fixtures_root / "previous_format" / name, tmp_path / name)

    expected = project_root / "tests" / "scrapers" / name / "scraper.json"
    assert warnings == []
    assert (tmp_path / name / "scraper.json").read_text(encoding="utf-8") == expected.read_text(
        encoding="utf-8"
    )


def previous(**sections) -> dict:
    return {"website": "https://shop.test", **sections}


def test_selectors_keep_xpath_priority_fallbacks_and_value_prepare() -> None:
    converted, warnings = convert(
        "shop",
        previous(
            xpath_options={
                "detail_page": {
                    "detail_fields": {
                        "price": {
                            "XPATH": "//span[@id='price']",
                            "CSS": ".price",
                            "REQUIRED": True,
                            "FALLBACK_SELECTORS": [{"CSS": ".old-price"}],
                            "VALUE_PREPARE": {"REGULAR_EXPRESSION": r"\s", "TYPE": "REMOVE"},
                        },
                        "title": {"RELATIVE_XPATH": "./h1", "XPATH": "/html/h1", "WAIT": 2},
                    }
                }
            }
        ),
    )

    template = converted["pages"]["detail"]["templates"][0]
    assert template["fields"]["price"] == {
        "selectors": [{"xpath": "//span[@id='price']"}, {"css": ".price"}, {"css": ".old-price"}],
        "prepare": {"regex": r"\s", "mode": "remove"},
    }
    assert template["fields"]["title"] == {"xpath": "./h1", "wait": 2}
    assert template["required_fields"] == ["price"]
    assert warnings == []
    ScraperDefinition.model_validate(converted)


def test_list_templates_map_item_samples_pagination_and_timing() -> None:
    converted, warnings = convert(
        "shop",
        previous(
            links=["https://shop.test/catalog/"],
            xpath_options={
                "paginator": {
                    "start_page": 1,
                    "last_page": 5,
                    "by_url": {"change_by_url": True, "template": "{URL}?page={page}"},
                    "next_page_link": {"CSS": "a.next"},
                    "stop_if_all_items_the_same": True,
                },
                "list_page": {
                    "template_name": "grid",
                    "first_item": {
                        "item_body": {"MAIN_XPATH": "//div[@id='grid']/div"},
                        "list_fields": {"link": {"XPATH": "//div[@id='grid']/div[1]/a"}},
                    },
                    "second_item": {"item_body": {}, "list_fields": {}},
                },
            },
            time_options={"wait_item_body": 3, "delay_before_close": [5, 10]},
            scroll_options={"scroll_to_list_items": True, "scroll_delta_y": [10, 20]},
        ),
    )

    page = converted["pages"]["list"]
    assert converted["links"] == [
        {"id": "main", "type": "list", "urls": ["https://shop.test/catalog/"]}
    ]
    assert page["templates"][0]["item"] == {"xpath": "//div[@id='grid']/div"}
    assert page["templates"][0]["next_page"] == {"css": "a.next"}
    assert page["pagination"]["url_template"] == "{url}?page={page}"
    assert page["pagination"]["stop_if_all_items_same"] is True
    assert page["waits"] == {"item_body": 3}
    assert page["scroll"] == {"delta_y": [10, 20], "enabled": True}
    assert warnings == []
    ScraperDefinition.model_validate(converted)


def test_unconverted_settings_are_reported_not_dropped_silently() -> None:
    _, warnings = convert(
        "shop",
        previous(
            page_state={"catalog": {"reload_on": ["blank", "has_items", "loading"]}},
            page_discovery={"enabled": True, "cache_enabled": True},
            proxy_check={"enabled": True, "health_check": {"enabled": True}, "browser_slow_mo": 5},
            surprise={"x": 1},
            xpath_options={"list_page": [], "detail_page": {}},
        ),
    )

    assert warnings == [
        ".surprise: not converted",
        "page_discovery.cache_enabled: runtime setting, not part of scraper.json v3",
        "proxy_check.browser_slow_mo: runtime setting, not part of scraper.json v3",
    ]


def test_reload_on_drops_states_without_v3_equivalent() -> None:
    converted, warnings = convert(
        "shop",
        previous(
            page_state={"catalog": {"reload_on": ["blank", "has_items", "loading"]}},
            xpath_options={
                "list_page": [
                    {"first_item": {"item_body": {"CSS": "li"}, "list_fields": {"a": {"CSS": "a"}}}}
                ]
            },
        ),
    )

    assert converted["pages"]["list"]["state"]["reload_on"] == ["blank", "loading"]
    assert warnings == [
        "page_state.catalog.reload_on: 'has_items' dropped, items mean the page is ready"
    ]


def test_prototype_format_is_rejected() -> None:
    with pytest.raises(UnsupportedFormatError, match="prototype format"):
        convert("shop", {"options": {}, "pages": {"list": {}}})

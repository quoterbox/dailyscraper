from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.actions import ScraperActions
from dailyscraper.execution.loader import PackageCodeError, load_package_class
from dailyscraper.execution.sink import JsonLinesSink
from dailyscraper.execution.timing import Pacer
from dailyscraper.page_scrapers.extraction import clean_text, read_value
from dailyscraper.page_scrapers.list_scraper import _same_items
from dailyscraper.pages import Selector
from dailyscraper.pipelines import DefaultPipeline
from dailyscraper.schema import ScraperPackage, load_scraper_package


class FakeElement:
    def __init__(self, text: str = "", attributes: dict[str, str] | None = None) -> None:
        self._text = text
        self._attributes = attributes or {}

    def text(self) -> str:
        return self._text

    def attribute(self, name: str) -> str | None:
        return self._attributes.get(name)


@pytest.mark.parametrize(
    ("element", "selector", "link", "expected"),
    [
        (FakeElement(" Ring;\n  gold <b> "), {"css": ".t"}, False, "Ring gold b"),
        (
            FakeElement(attributes={"href": "/p/ring/"}),
            {"css": "a"},
            True,
            "https://shop.test/p/ring/",
        ),
        (
            FakeElement(attributes={"src": "img/1.png"}),
            {"css": "img", "attribute": "src"},
            False,
            "https://shop.test/catalog/img/1.png",
        ),
        (
            FakeElement("1 299 rub"),
            {"css": ".p", "prepare": {"regex": r"\D", "mode": "remove"}},
            False,
            "1299",
        ),
        (FakeElement("no price"), {"css": ".p", "prepare": {"regex": r"\d+"}}, False, None),
        (FakeElement(attributes={}), {"css": "a", "attribute": "data-id"}, False, None),
    ],
)
def test_field_values_are_read_prepared_and_cleaned(element, selector, link, expected) -> None:
    value = read_value(
        element,
        Selector.model_validate(selector),
        base_url="https://shop.test/catalog/",
        link=link,
    )

    assert value == expected


def test_clean_text_collapses_whitespace_instead_of_gluing_lines() -> None:
    assert clean_text("Line one\r\nLine two\t end") == "Line one Line two end"


@pytest.mark.parametrize(
    ("previous", "current", "only_if_all_same", "expected"),
    [
        ([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 2}], False, True),
        ([{"a": 1}, {"a": 2}], [{"a": 2}, {"a": 3}], False, True),
        ([{"a": 1}, {"a": 2}], [{"a": 2}, {"a": 3}], True, False),
        ([{"a": 1}], [{"a": 3}, {"a": 4}], False, False),
        ([], [{"a": 1}], False, False),
    ],
)
def test_same_content_rule_matches_the_previous_implementation(
    previous, current, only_if_all_same, expected
) -> None:
    assert _same_items(previous, current, only_if_all_same) is expected


def test_pacer_checks_for_interruption_while_sleeping() -> None:
    sleeps: list[float] = []
    checks: list[int] = []
    pacer = Pacer(lambda: checks.append(1), sleep=sleeps.append)

    pacer.pause(0)
    pacer.delay((0, 0))

    assert sleeps == []
    assert len(checks) == 2


def test_jsonl_sink_writes_one_line_per_item(tmp_path: Path) -> None:
    path = tmp_path / "out" / "items.jsonl"
    with JsonLinesSink(path) as sink:
        sink.emit({"name": "Кольцо", "price": None})
        sink.emit({"name": "Ring"})

    assert sink.count == 2
    assert path.read_text(encoding="utf-8").splitlines() == [
        '{"name": "Кольцо", "price": null}',
        '{"name": "Ring"}',
    ]


def make_package(tmp_path: Path, name: str, files: dict[str, str]) -> ScraperPackage:
    root = tmp_path / name
    root.mkdir()
    definition = {
        "schema_version": 3,
        "name": name,
        "website": "https://shop.test",
        "pages": {"detail": {"templates": [{"name": "d", "fields": {"t": {"css": "h1"}}}]}},
    }
    (root / "scraper.json").write_text(json.dumps(definition), encoding="utf-8")
    for file_name, code in files.items():
        (root / file_name).write_text(code, encoding="utf-8")
    return load_scraper_package(root, variables={})


def test_package_classes_default_to_the_framework_ones(tmp_path: Path) -> None:
    package = make_package(tmp_path, "plain_shop", {})

    assert load_package_class(package, "actions", "Actions", ScraperActions) is ScraperActions
    assert load_package_class(package, "pipeline", "Pipeline", DefaultPipeline) is DefaultPipeline


def test_package_actions_may_use_relative_imports(tmp_path: Path) -> None:
    package = make_package(
        tmp_path,
        "api_shop",
        {
            "api.py": "MARKER = 'from-api'\n",
            "actions.py": (
                "from dailyscraper.actions import ScraperActions\n"
                "from .api import MARKER\n\n"
                "class Actions(ScraperActions):\n"
                "    marker = MARKER\n"
            ),
        },
    )

    actions_class = load_package_class(package, "actions", "Actions", ScraperActions)

    assert issubclass(actions_class, ScraperActions)
    assert actions_class.marker == "from-api"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("class Other:\n    pass\n", "must define class Actions"),
        ("class Actions:\n    pass\n", "must define class Actions"),
        ("raise RuntimeError('broken package')\n", "cannot import"),
    ],
)
def test_invalid_package_code_is_reported(tmp_path: Path, code: str, message: str) -> None:
    package = make_package(tmp_path, f"bad_shop_{abs(hash(code))}", {"actions.py": code})

    with pytest.raises(PackageCodeError, match=message):
        load_package_class(package, "actions", "Actions", ScraperActions)

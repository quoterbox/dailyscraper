from __future__ import annotations

import pytest

from dailyscraper.pages.xpath import (
    XPathError,
    derive_item_xpath,
    item_xpath,
    relative_to_item,
    split_xpath,
)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("/html/body/div[2]/div[2]", "/html/body/div[2]/div[3]", "/html/body/div[2]/div"),
        ("/html/body/ul/li[9]", "/html/body/ul/li[10]", "/html/body/ul/li"),
        ("/html/body/ul/li[12]", "/html/body/ul/li[13]", "/html/body/ul/li"),
        ("/html/body/ul/li[1]/a/h2", "/html/body/ul/li[2]/a/h2", "/html/body/ul/li"),
        (
            '//*[@id="root"]/div[@data-href="/a/b"]/section[1]/span',
            '//*[@id="root"]/div[@data-href="/a/b"]/section[2]/span',
            '//*[@id="root"]/div[@data-href="/a/b"]/section',
        ),
    ],
)
def test_item_collection_is_derived_from_two_samples(first, second, expected) -> None:
    assert derive_item_xpath(first, second) == expected


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        ("/html/body/div[2]/div[2]", "/html/body/div[2]/div[2]", "identical"),
        ("/html/body/div[2]/span", "/html/body/div[2]", "different depth"),
        ("/html/body/div[2]", "/html/body/span[2]", "only by a positional index"),
        ("/html/div[1]/ul/li[1]", "/html/div[2]/ul/li[2]", "more than one step"),
        ("/html/body/div[2]", "/html/body/div", "only by a positional index"),
    ],
)
def test_invalid_samples_are_rejected(first, second, message) -> None:
    with pytest.raises(XPathError, match=message):
        derive_item_xpath(first, second)


def test_split_keeps_descendant_steps_and_quoted_slashes() -> None:
    assert split_xpath("//div[@a='x/y']/span") == ["", "", "div[@a='x/y']", "span"]


def test_unbalanced_xpath_is_rejected() -> None:
    with pytest.raises(XPathError, match="unbalanced"):
        split_xpath("/html/div[@a='x]")


@pytest.mark.parametrize(
    ("absolute", "expected"),
    [
        ("/html/body/ul/li[3]/a/h2", "./a/h2"),
        ("/html/body/ul/li[1]", "."),
    ],
)
def test_absolute_xpath_becomes_relative_to_the_item(absolute, expected) -> None:
    assert relative_to_item("/html/body/ul/li", absolute) == expected


@pytest.mark.parametrize(
    "absolute",
    ["/html/body/ol/li[1]/a", "/html/body/ul/div[1]/a", "/html/body/ul/li/a"],
)
def test_xpath_outside_items_cannot_be_made_relative(absolute) -> None:
    with pytest.raises(XPathError, match="not inside an item"):
        relative_to_item("/html/body/ul/li", absolute)


def test_item_positions_start_at_one() -> None:
    assert item_xpath("/html/body/ul/li", 4) == "/html/body/ul/li[4]"
    with pytest.raises(XPathError):
        item_xpath("/html/body/ul/li", 0)

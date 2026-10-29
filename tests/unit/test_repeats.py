"""Repeated catalog pages: the detector and how list pages and discovery react to it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dailyscraper.actions import ScraperActions
from dailyscraper.domain import PageOutcome, PageState, PageType
from dailyscraper.execution.partitions import ItemIdentity
from dailyscraper.page_scrapers import ListPageScraper, RepeatDetector
from dailyscraper.pages import ListPage, Repeats
from dailyscraper.pipelines import DefaultPipeline
from dailyscraper.schema import ScraperDefinition
from dailyscraper.transport.page import Navigation


def names(*values: str) -> list[dict[str, Any]]:
    return [{"name": value} for value in values]


def detector(**settings) -> RepeatDetector:
    return RepeatDetector(Repeats(**settings), ItemIdentity().key)


@pytest.mark.parametrize(
    ("match", "current", "expected"),
    [
        ("all", ("a", "b"), True),
        ("all", ("b", "c"), False),
        ("any", ("b", "c"), True),
        ("any", ("c", "d"), False),
        ("share", ("a", "b", "c", "d", "e"), False),
        ("share", ("a", "b", "c", "d"), True),
    ],
)
def test_match_rules(match: str, current: tuple[str, ...], expected: bool) -> None:
    repeats = detector(match=match, min_share=0.5)
    repeats.remember(1, names("a", "b"))

    assert repeats.check(names(*current)).repeated is expected


@pytest.mark.parametrize(
    ("previous", "current", "only_if_all_same", "expected"),
    [
        (("a", "b"), ("a", "b"), False, True),
        (("a", "b"), ("b", "c"), False, True),
        (("a", "b"), ("b", "c"), True, False),
        (("a",), ("c", "d"), False, False),
    ],
)
def test_converted_rules_match_the_previous_implementation(
    previous, current, only_if_all_same, expected
) -> None:
    repeats = detector(match="all" if only_if_all_same else "any")
    repeats.remember(1, names(*previous))

    assert repeats.check(names(*current)).repeated is expected


def test_previous_page_only_or_every_page_seen() -> None:
    previous = detector()
    seen = detector(compare_with="seen")
    for repeats in (previous, seen):
        repeats.remember(1, names("a", "b"))
        repeats.remember(2, names("c", "d"))

    assert not previous.check(names("a", "b")).repeated
    found = seen.check(names("a", "b"))
    assert (found.repeated, found.page, found.reason) == (True, 1, "repeats_page_1")


def test_nothing_repeats_when_disabled_or_before_the_first_page() -> None:
    off = detector(enabled=False)
    off.remember(1, names("a"))

    assert not off.check(names("a")).repeated
    assert not detector().check(names("a")).repeated
    assert not detector(match="any").check([]).repeated


def test_keys_follow_identity_fields_then_links_then_content() -> None:
    by_id = RepeatDetector(Repeats(), ItemIdentity(fields=("sku",)).key)
    by_id.remember(1, [{"sku": "1", "price": "10"}])

    assert by_id.check([{"sku": "1", "price": "12"}]).repeated
    by_content = detector()
    by_content.remember(1, [{"sku": "1", "price": "10"}])
    assert not by_content.check([{"sku": "1", "price": "12"}]).repeated


class Shop:
    """A catalog of ``last`` pages that answers any later page with the last one.

    ``lag`` clicks show the previous page once more before the next one appears.
    """

    def __init__(self, last: int, *, lag: int = 0) -> None:
        self.last = last
        self.lag = lag
        self.page = 1
        self.shown = 1
        self.opened: list[int] = []

    def items(self, page: int) -> list[str]:
        page = min(page, self.last)
        return [f"p{page}-{n}" for n in range(1, 4)]

    def open(self, page: int) -> None:
        self.opened.append(page)
        self.page = self.shown = page

    def click_next(self) -> None:
        self.page += 1
        if not self.lag:
            self.shown = self.page

    def settle(self) -> None:
        if self.shown != self.page:
            self.lag -= 1
            if self.lag <= 0:
                self.shown = self.page


class Field:
    def __init__(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value

    def attribute(self, name: str) -> str:
        return f"/product/{self.value}"


class Item:
    def __init__(self, name: str) -> None:
        self.name = name

    def find(self, selector, wait: float = 0) -> Field:
        return Field(self.name)

    def scroll_into_view(self) -> None:
        pass


class Button:
    def __init__(self, shop: Shop) -> None:
        self.shop = shop

    def scroll_into_view(self) -> None:
        pass

    def click(self) -> None:
        self.shop.click_next()


class Finder:
    def __init__(self, shop: Shop) -> None:
        self.shop = shop

    def find(self, selector, wait: float = 0):
        if selector.locators[0].value == "a.next":
            return Button(self.shop)
        return object()

    def find_all(self, selector, wait: float = 0) -> list[Item]:
        return [Item(name) for name in self.shop.items(self.shop.shown)]

    def count(self, selector) -> int:
        return 3


class Navigator:
    def __init__(self, shop: Shop) -> None:
        self.shop = shop

    def open(self, url: str, page_type, allow_reload: bool = True) -> PageOutcome:
        self.navigate(url)
        return self._outcome(url)

    def navigate(self, url: str) -> Navigation:
        self.shop.open(int(url.rsplit("=", 1)[1]))
        return Navigation(url, url, 200)

    def wait(self, page_type, allow_reload: bool = True) -> PageOutcome:
        self.shop.settle()
        return self._outcome(f"https://shop.test/c?page={self.shop.shown}")

    def _outcome(self, url: str) -> PageOutcome:
        return PageOutcome(PageState.READY, PageType.LIST, url, "ready", items_count=3)


class Pacer:
    def delay(self, bounds) -> None:
        pass

    def pause(self, seconds: float) -> None:
        pass

    def check_interrupted(self) -> None:
        pass


class Sink:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def emit(self, row, **_: Any) -> None:
        self.rows.append(row)


def definition(*, by_url: bool, repeats: dict[str, Any] | None = None, **discovery) -> Any:
    pagination: dict[str, Any] = {"repeats": repeats or {}}
    if by_url:
        pagination["url_template"] = "{url}?page={page}"
    return ScraperDefinition.model_validate(
        {
            "schema_version": 3,
            "name": "shop",
            "website": "https://shop.test",
            "links": [{"id": "main", "urls": ["https://shop.test/c"]}],
            "discovery": {"enabled": True, "probe_wait_seconds": 0, **discovery},
            "pages": {
                "list": {
                    "templates": [
                        {
                            "name": "grid",
                            "ready": {"css": "li"},
                            "first_item": {
                                "body": {"css": "li"},
                                "fields": {"name": {"css": ".name"}, "detail_link": {"css": "a"}},
                            },
                            "next_page": {"css": "a.next"},
                        }
                    ],
                    "pagination": pagination,
                }
            },
        }
    )


def context(shop: Shop, spec: Any) -> SimpleNamespace:
    page = SimpleNamespace(url="https://shop.test/c", body_text=lambda: "")
    finder = Finder(shop)
    navigator = Navigator(shop)
    navigator.page = page
    actions = ScraperActions(page, finder, {}, lambda: None)
    return SimpleNamespace(
        definition=spec,
        page=page,
        finder=finder,
        navigator=navigator,
        actions=actions,
        sink=Sink(),
        pacer=Pacer(),
    )


def first(shop: Shop, page: int = 1) -> PageOutcome:
    shop.open(page)
    return PageOutcome(
        PageState.READY, PageType.LIST, "https://shop.test/c", "ready", items_count=3
    )


def test_a_page_past_the_end_ends_the_link_after_confirmations() -> None:
    shop = Shop(3)
    spec = definition(by_url=True, repeats={"confirmations": 2})
    scraper = ListPageScraper(context(shop, spec), spec.pages.list)

    result = scraper.run("https://shop.test/c", first(shop))

    assert result.stop_reason == "repeated_page:repeats_page_3"
    assert len(result.items) == 9
    assert shop.opened == [1, 2, 3, 4, 4]


def test_a_slow_click_is_waited_out_without_duplicates() -> None:
    shop = Shop(3, lag=1)
    spec = definition(by_url=False, repeats={"confirmations": 2})
    spec = spec.model_copy(
        update={"pages": spec.pages.model_copy(update={"list": with_last_page(spec, 3)})}
    )
    scraper = ListPageScraper(context(shop, spec), spec.pages.list)

    result = scraper.run("https://shop.test/c", first(shop))

    assert result.stop_reason == "last_page"
    assert [item["name"] for item in result.items] == [
        f"p{page}-{n}" for page in (1, 2, 3) for n in (1, 2, 3)
    ]


def with_last_page(spec: Any, last: int) -> ListPage:
    page = spec.pages.list
    return page.model_copy(
        update={"pagination": page.pagination.model_copy(update={"last_page": last})}
    )


class PromotedShop(Shop):
    def items(self, page: int) -> list[str]:
        return ["promo", *super().items(page)]


def test_one_promoted_item_on_every_page_does_not_end_discovery() -> None:
    shop = PromotedShop(5)
    spec = definition(by_url=True, repeats={"match": "any"})

    result = DefaultPipeline(context(shop, spec)).discover("https://shop.test/c")

    assert (result.status, result.last_page) == ("ok", 5)


@pytest.mark.parametrize("save_items", [True, False])
def test_discovery_ends_where_pages_start_repeating(save_items: bool) -> None:
    shop = Shop(5)
    spec = definition(by_url=True, repeats={"match": "any"}, save_items=save_items)
    ctx = context(shop, spec)

    result = DefaultPipeline(ctx).discover("https://shop.test/c")

    assert (result.status, result.last_page) == ("ok", 5)
    assert result.saved_pages == ((1, 2, 3, 5) if save_items else ())
    assert len(ctx.sink.rows) == (12 if save_items else 0)
    assert shop.opened[-3:] == [9, 7, 6]

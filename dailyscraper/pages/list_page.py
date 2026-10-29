from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from dailyscraper.pages.base import DelayRange, FrozenModel, Seconds
from dailyscraper.pages.common import ItemSample, Scroll
from dailyscraper.pages.selectors import Selector
from dailyscraper.pages.state import PageStateRules


class ListTemplate(FrozenModel):
    name: str = Field(min_length=1)
    ready: Selector | None = None
    stop: Selector | None = None
    item: Selector | None = None
    first_item: ItemSample
    second_item: ItemSample | None = None
    next_page: Selector | None = None


class Repeats(FrozenModel):
    """When a catalog page counts as a repeat of pages already read."""

    enabled: bool = True
    match: Literal["all", "any", "share"] = "all"
    min_share: float = Field(0.8, gt=0, le=1)
    compare_with: Literal["previous", "seen"] = "previous"
    confirmations: int = Field(2, ge=1)
    key_fields: tuple[str, ...] = ()


class Pagination(FrozenModel):
    start_page: int = Field(1, ge=1)
    last_page: int = Field(0, ge=0, description="0 means until the site has no next page")
    url_template: str | None = None
    infinity_loading: bool = False
    repeats: Repeats = Repeats()

    @model_validator(mode="after")
    def _consistent(self) -> Pagination:
        if self.last_page and self.last_page < self.start_page:
            raise ValueError("last_page must not be less than start_page")
        if self.url_template is not None and "{page}" not in self.url_template:
            raise ValueError("url_template must contain the {page} placeholder")
        return self


class ListWaits(FrozenModel):
    item_body: Seconds = 10
    item: Seconds = 10
    field: Seconds = 1
    next_page: Seconds = 1
    last_page: Seconds = 2


class ListDelays(FrozenModel):
    before_next_page: DelayRange = (0, 0)
    between_items: DelayRange = (0, 0)


class ListPage(FrozenModel):
    templates: tuple[ListTemplate, ...] = Field(min_length=1)
    pagination: Pagination = Pagination()
    start_item: int = Field(1, ge=1)
    last_item: int = Field(0, ge=0)
    detail_link_field: str = "detail_link"
    open_detail_by_click: bool = False
    save_start_link: bool = False
    waits: ListWaits = ListWaits()
    delays: ListDelays = ListDelays()
    scroll: Scroll = Scroll()
    state: PageStateRules | None = None

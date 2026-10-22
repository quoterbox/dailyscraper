from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from dailyscraper.pages import AuthPage, DetailPage, ListPage, PageStateRules
from dailyscraper.pages.base import DelayRange, FrozenModel, Seconds

SCRAPER_SCHEMA_VERSION = 3
SCRAPER_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class LinkGroup(FrozenModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    type: Literal["list", "template"] = "list"
    urls: tuple[str, ...] = ()
    url_template: str | None = None

    @model_validator(mode="after")
    def _matches_type(self) -> LinkGroup:
        if self.type == "list" and (not self.urls or self.url_template):
            raise ValueError("a 'list' link group needs urls and no url_template")
        if self.type == "template" and (self.urls or not self.url_template):
            raise ValueError("a 'template' link group needs url_template and no urls")
        return self


class Navigation(FrozenModel):
    warmup_urls: tuple[str, ...] = ()
    open_target_after_warmup: bool = True
    delay_after_warmup: DelayRange = (0, 0)


class Discovery(FrozenModel):
    enabled: bool = False
    probe_wait_seconds: Seconds = 8
    settle_seconds: Seconds = 2
    empty_confirmations: int = Field(1, ge=1)
    unknown_retries: int = Field(2, ge=0)
    on_unknown: Literal[
        "fallback_first_chunk", "fallback_config_limit", "skip_link", "fail_run"
    ] = "fallback_first_chunk"
    on_blocked: Literal["skip_link", "fallback_first_chunk", "fail_run"] = "skip_link"
    empty_when_no_items_text_markers: tuple[str, ...] = ()


class HealthCheck(FrozenModel):
    enabled: bool = True
    blocking: bool = False
    urls: tuple[str, ...] = ()
    timeout_seconds: Seconds | None = None
    min_items: int = Field(1, ge=1)
    required_fields: tuple[str, ...] = ()
    detail_required_fields: tuple[str, ...] = ()
    check_detail: Literal["auto", "always", "never"] = "auto"
    check_all_proxies: bool = False
    max_sample_urls: int = Field(5, ge=1)


class Inputs(FrozenModel):
    encoding: str = "utf-8-sig"
    delimiter: str = Field(";", min_length=1, max_length=1)
    detail_link_field: str = "detail_link"
    required_fields: tuple[str, ...] = ("detail_link",)
    identity_fields: tuple[str, ...] = ()
    deduplicate_by_fields: tuple[str, ...] = ()


class Pages(FrozenModel):
    auth: AuthPage | None = None
    list: ListPage | None = None
    detail: DetailPage | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Pages:
        if self.list is None and self.detail is None:
            raise ValueError("a scraper needs at least a list or a detail page")
        return self


class ScraperDefinition(FrozenModel):
    schema_version: Literal[3]
    name: str = Field(pattern=SCRAPER_NAME_PATTERN)
    website: str = Field(pattern=r"^https?://")
    links: tuple[LinkGroup, ...] = ()
    navigation: Navigation = Navigation()
    page_state: PageStateRules | None = None
    discovery: Discovery = Discovery()
    health_check: HealthCheck | None = None
    inputs: Inputs | None = None
    pages: Pages
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form settings read only by the package's own hooks",
    )

    @model_validator(mode="after")
    def _unique_link_ids(self) -> ScraperDefinition:
        ids = [group.id for group in self.links]
        if len(ids) != len(set(ids)):
            raise ValueError("link group ids must be unique")
        return self

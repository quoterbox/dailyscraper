from __future__ import annotations

from typing import Literal

from pydantic import Field

from dailyscraper.domain import PageState
from dailyscraper.pages.base import FrozenModel, Seconds
from dailyscraper.pages.selectors import Selector


class PageStateRules(FrozenModel):
    """Page state detection settings; ``None`` means "inherit or use the framework default"."""

    wait_attempts: int | None = Field(None, ge=0)
    wait_interval_ms: int | None = Field(None, ge=0)
    reload_attempts: int | None = Field(None, ge=0)
    reload_wait_ms: int | None = Field(None, ge=0)
    reload_on: tuple[PageState, ...] | None = None
    settle_seconds: Seconds | None = None
    activity_after_open: bool | None = None
    activity_after_reload: bool | None = None
    body_is_ready: bool | None = None
    ready_mode: Literal["any", "all"] | None = None
    prefer_ready_selectors: bool | None = None
    ready_selectors: tuple[Selector, ...] | None = None
    loading_selectors: tuple[Selector, ...] | None = None
    empty_selectors: tuple[Selector, ...] | None = None
    empty_after_wait_selectors: tuple[Selector, ...] | None = None
    not_found_selectors: tuple[Selector, ...] | None = None
    json_ld_types: tuple[str, ...] | None = None
    loading_text_markers: tuple[str, ...] | None = None
    empty_text_markers: tuple[str, ...] | None = None
    not_found_text_markers: tuple[str, ...] | None = None
    captcha_text_markers: tuple[str, ...] | None = None
    blocked_text_markers: tuple[str, ...] | None = None
    proxy_auth_text_markers: tuple[str, ...] | None = None
    browser_error_text_markers: tuple[str, ...] | None = None

    def overridden_by(self, specific: PageStateRules | None) -> PageStateRules:
        if specific is None:
            return self
        overrides = {
            name: value
            for name in type(specific).model_fields
            if (value := getattr(specific, name)) is not None
        }
        return self.model_copy(update=overrides)

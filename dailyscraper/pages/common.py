# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pydantic import Field

from dailyscraper.pages.base import FrozenModel, OffsetRange
from dailyscraper.pages.selectors import Selector


class ItemSample(FrozenModel):
    body: Selector | None = None
    fields: dict[str, Selector] = Field(default_factory=dict)


class Scroll(FrozenModel):
    enabled: bool = False
    origin_x: OffsetRange = (0, 0)
    origin_y: OffsetRange = (0, 0)
    delta_x: OffsetRange = (0, 0)
    delta_y: OffsetRange = (0, 0)

# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Page executors for list, detail and auth pages."""

from dailyscraper.page_scrapers.kinds.auth import AuthPageScraper
from dailyscraper.page_scrapers.kinds.detail import DetailPageScraper, DetailRowResult
from dailyscraper.page_scrapers.kinds.list import (
    ListPageScraper,
    ListPageVisit,
    ListResult,
    PageRange,
)
from dailyscraper.page_scrapers.repeats import ItemKey, RepeatCheck, RepeatDetector

__all__ = [
    "AuthPageScraper",
    "DetailPageScraper",
    "DetailRowResult",
    "ListPageScraper",
    "ListPageVisit",
    "ListResult",
    "ItemKey",
    "PageRange",
    "RepeatCheck",
    "RepeatDetector",
]

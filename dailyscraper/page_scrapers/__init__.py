"""Page executors for list, detail and auth pages."""

from dailyscraper.page_scrapers.auth_scraper import AuthPageScraper
from dailyscraper.page_scrapers.detail_scraper import DetailPageScraper, DetailRowResult
from dailyscraper.page_scrapers.list_scraper import (
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

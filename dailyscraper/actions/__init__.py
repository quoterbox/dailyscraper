"""ScraperActions: hooks a scraper package can override."""

from dailyscraper.actions.base import ScraperActions
from dailyscraper.domain import ErrorCategory, PageError

__all__ = ["ErrorCategory", "PageError", "ScraperActions"]

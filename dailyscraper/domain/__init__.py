"""Pure domain types without I/O."""

from dailyscraper.domain.errors import ErrorCategory
from dailyscraper.domain.pages import PageOutcome, PageState, PageType

__all__ = ["ErrorCategory", "PageOutcome", "PageState", "PageType"]

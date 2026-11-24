# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""ScraperActions: hooks a scraper package can override."""

from dailyscraper.actions.base import ScraperActions
from dailyscraper.domain import ErrorCategory, PageError

__all__ = ["ErrorCategory", "PageError", "ScraperActions"]

# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Validated models of scraper.json, runtime and workload configuration."""

from dailyscraper.schema.package import (
    ScraperPackage,
    ScraperPackageError,
    load_scraper_package,
)
from dailyscraper.schema.scraper import (
    SCRAPER_SCHEMA_VERSION,
    Discovery,
    HealthCheck,
    Inputs,
    LinkGroup,
    Navigation,
    Pages,
    ScraperDefinition,
)

__all__ = [
    "SCRAPER_SCHEMA_VERSION",
    "Discovery",
    "HealthCheck",
    "Inputs",
    "LinkGroup",
    "Navigation",
    "Pages",
    "ScraperDefinition",
    "ScraperPackage",
    "ScraperPackageError",
    "load_scraper_package",
]

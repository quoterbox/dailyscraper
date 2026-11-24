# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Proxy pool, health shared by all runs, allocation and network checks."""

from dailyscraper.proxies.allocator import ProxyAllocator
from dailyscraper.proxies.health import HealthTransition, ProxyHealth, effective_status, record
from dailyscraper.proxies.network_check import CheckResult, check_proxies
from dailyscraper.proxies.sources import ProxyPool, ProxySourceError, build_pool, redact_url

__all__ = [
    "CheckResult",
    "HealthTransition",
    "ProxyAllocator",
    "ProxyHealth",
    "ProxyPool",
    "ProxySourceError",
    "build_pool",
    "check_proxies",
    "effective_status",
    "record",
    "redact_url",
]

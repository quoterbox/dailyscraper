# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""SQLite implementation of the storage contracts."""

from dailyscraper.storage.sqlite.store import SQLiteStore, utc_now

__all__ = ["SQLiteStore", "utc_now"]

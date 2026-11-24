# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Dispatcher, gates and fairness: the parent-side owner of task order and retries."""

from dailyscraper.dispatch.dispatcher import Dispatcher, DispatchResult
from dailyscraper.dispatch.gates import Gates, GateState

__all__ = ["DispatchResult", "Dispatcher", "GateState", "Gates"]

# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Default pipeline and the context passed to scraper pipelines."""

from dailyscraper.pipelines.default import DefaultPipeline, LinkResult, PipelineResult

__all__ = ["DefaultPipeline", "LinkResult", "PipelineResult"]

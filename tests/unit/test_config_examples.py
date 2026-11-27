# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

from dailyscraper.settings.models import Settings
from tools.config_examples import EXAMPLES, render


def test_the_template_examples_show_every_current_setting() -> None:
    expected = render()

    actual = {path.name: path.read_text("utf-8") for path in EXAMPLES.glob("*.json")}

    assert actual == expected, "run: uv run python -m tools.config_examples"


def test_the_examples_together_are_valid_settings() -> None:
    merged = {}
    for text in render().values():
        merged.update(json.loads(text))

    Settings.model_validate({"environment": "example", "project_root": ".", **merged})

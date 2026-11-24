# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

from tools.license_headers import header, outdated, with_header

HEADER = "# Copyright (c) 2026 JQ.\n#\n# SPDX-License-Identifier: MIT\n"


def test_every_python_file_starts_with_the_license_header() -> None:
    stale = [str(path) for path in outdated()]

    assert stale == [], "run: uv run python tools/license_headers.py --fix"


def test_the_header_goes_first_and_an_old_one_is_replaced() -> None:
    code = '"""Module."""\n\nimport os\n'
    old = "# Copyright (c) 2025 Someone.\n# SPDX-License-Identifier: MIT\n\n" + code

    assert with_header(code, HEADER) == HEADER + "\n" + code
    assert with_header(old, HEADER) == HEADER + "\n" + code
    assert with_header("", HEADER) == HEADER
    assert with_header(with_header(code, HEADER), HEADER) == HEADER + "\n" + code


def test_the_template_is_the_one_from_tools() -> None:
    template = Path(__file__).resolve().parents[2] / "tools" / "license_header.txt"

    assert header() == template.read_text(encoding="utf-8").strip() + "\n"
    assert "SPDX-License-Identifier: MIT" in header()

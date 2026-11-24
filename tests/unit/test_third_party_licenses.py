# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

from tools.third_party_licenses import OUTPUT, generate, locked_groups

LOCK = """
[[package]]
name = "app"
version = "0.0.0"
dependencies = [{ name = "lib" }]

[package.dev-dependencies]
dev = [{ name = "tester" }]

[[package]]
name = "lib"
version = "1.0"
dependencies = [{ name = "shared" }]

[[package]]
name = "tester"
version = "2.0"
dependencies = [{ name = "shared" }, { name = "plugin" }]

[[package]]
name = "shared"
version = "3.0"

[[package]]
name = "plugin"
version = "4.0"
"""


def test_the_notices_file_matches_the_locked_dependencies() -> None:
    current = OUTPUT.read_bytes().decode("utf-8").replace("\r\n", "\n")

    assert current == generate(), "run: uv run python tools/third_party_licenses.py --write"


def test_runtime_and_development_packages_come_from_the_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tools.third_party_licenses.PROJECT", "app")
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")

    runtime, development = locked_groups(lock)

    assert runtime == {"lib": "1.0", "shared": "3.0"}
    assert development == {"tester": "2.0", "plugin": "4.0"}

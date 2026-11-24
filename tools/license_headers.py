# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Keep the license header of ``tools/license_header.txt`` at the top of every Python file.

``--fix`` adds the header where it is missing and replaces an outdated one, so
changing the template and running the tool updates every file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "tools" / "license_header.txt"
SOURCE_DIRECTORIES = ("dailyscraper", "tests", "tools")
HEADER_START = "# Copyright"


def header() -> str:
    return TEMPLATE.read_text(encoding="utf-8").strip() + "\n"


def python_files(root: Path = ROOT) -> list[Path]:
    return sorted(
        path
        for directory in SOURCE_DIRECTORIES
        for path in (root / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def with_header(text: str, expected: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith(HEADER_START):
        end = 0
        while end < len(lines) and lines[end].startswith("#"):
            end += 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        lines = lines[end:]
    body = "".join(lines)
    return expected + ("\n" + body if body else "")


def outdated(root: Path = ROOT) -> list[Path]:
    expected = header()
    return [path for path in python_files(root) if with_header(read(path), expected) != read(path)]


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or add the license header.")
    parser.add_argument("--fix", action="store_true", help="add or update the headers")
    args = parser.parse_args()
    expected = header()
    stale = outdated()
    for path in stale:
        if args.fix:
            path.write_bytes(with_header(read(path), expected).encode("utf-8"))
        print(f"{'updated' if args.fix else 'missing or outdated'}: {path.relative_to(ROOT)}")
    if stale and not args.fix:
        print("run: uv run python tools/license_headers.py --fix")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

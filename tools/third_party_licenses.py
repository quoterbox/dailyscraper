# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Write THIRD_PARTY_LICENSES from uv.lock and the license files of the installed packages.

Runtime dependencies are listed with their full license texts; development tools
only in the summary, since they are not installed with DailyScraper. Licenses a
wheel does not ship are kept in ``tools/licenses/<package>/``.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "uv.lock"
OUTPUT = ROOT / "THIRD_PARTY_LICENSES"
SHIPPED_ELSEWHERE = ROOT / "tools" / "licenses"
PROJECT = "dailyscraper"
LICENSE_FILE_MARKERS = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
# Metadata that names no SPDX expression, checked against the package's license file.
LICENSE_OVERRIDES = {"colorama": "BSD-3-Clause"}
SOURCE_LABELS = ("source", "source code", "repository", "homepage")
# Apache-2.0 notices that a wheel keeps outside its dist-info.
NOTICE_FILES = {"playwright": ("playwright/driver/package/NOTICE",)}
RULE = "-" * 78
PLAYWRIGHT_BUNDLE = """\
The Playwright wheel also bundles a Node.js runtime and the Playwright driver.
Their notices are installed with it, in site-packages/playwright/driver/:
LICENSE (Node.js) and package/ThirdPartyNotices.txt (the driver's own
dependencies)."""
HEADER = """\
DailyScraper - Third-Party Software Notices
===========================================

DailyScraper (https://dailyscraper.com) is licensed under the MIT License; see
LICENSE.md. It uses the third-party software listed below, each under its own
license. DailyScraper does not include their source code: they are installed
from PyPI as dependencies. The full license texts of the runtime dependencies
are reproduced here for anyone who redistributes DailyScraper together with
them, for example in a container image.

This file is generated from uv.lock by tools/third_party_licenses.py.
Do not edit it by hand: update the dependencies and run
uv run python tools/third_party_licenses.py --write
"""
DOWNLOADED = """\
Components downloaded at run time
---------------------------------

Browsers are not dependencies of DailyScraper and are not covered by this file:
the builds that `playwright install` downloads (Chromium and others) and Google
Chrome, used when browser.playwright.channel is "chrome", come under their own
terms from their vendors."""


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    license: str
    source: str
    texts: tuple[tuple[str, str], ...]


def locked_groups(lock_path: Path = LOCK) -> tuple[dict[str, str], dict[str, str]]:
    """Runtime and development packages with their locked versions."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    root = packages[PROJECT]

    def closure(names: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        pending = list(names)
        while pending:
            name = pending.pop()
            if name in found or name == PROJECT:
                continue
            found[name] = packages[name]["version"]
            pending += [dependency["name"] for dependency in packages[name].get("dependencies", ())]
        return found

    runtime = closure([dependency["name"] for dependency in root.get("dependencies", ())])
    tools = [
        dependency["name"]
        for group in root.get("dev-dependencies", {}).values()
        for dependency in group
    ]
    development = {name: version for name, version in closure(tools).items() if name not in runtime}
    return runtime, development


def describe(name: str, version: str, *, with_texts: bool) -> Package:
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        if name not in LICENSE_OVERRIDES or with_texts:
            raise
        return Package(name, version, LICENSE_OVERRIDES[name], pypi(name), ())
    if distribution.version != version:
        raise RuntimeError(f"{name} {distribution.version} is installed, uv.lock has {version}")
    fields = distribution.metadata
    texts = license_texts(name, distribution) if with_texts else ()
    return Package(name, version, license_of(name, fields), source_of(name, fields), texts)


def license_of(name: str, fields) -> str:
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]
    expression = fields.get("License-Expression") or fields.get("License")
    if expression and "\n" not in expression and len(expression) < 60:
        return expression
    raise RuntimeError(f"{name}: no SPDX license expression; add it to LICENSE_OVERRIDES")


def source_of(name: str, fields) -> str:
    urls = {}
    for entry in fields.get_all("Project-URL") or ():
        label, _, url = entry.partition(",")
        urls[label.strip().lower()] = url.strip()
    for label in SOURCE_LABELS:
        if label in urls:
            return urls[label]
    return fields.get("Home-page") or pypi(name)


def pypi(name: str) -> str:
    return f"https://pypi.org/project/{name}/"


def license_texts(name: str, distribution) -> tuple[tuple[str, str], ...]:
    stored = SHIPPED_ELSEWHERE / name
    if stored.is_dir():
        paths = sorted(stored.iterdir(), key=lambda path: (path.suffix == ".python", path.name))
        return tuple((path.name, read(path)) for path in paths)
    files = [
        file
        for file in distribution.files or ()
        if ".dist-info" in file.parts[0]
        and any(marker in file.name.upper() for marker in LICENSE_FILE_MARKERS)
    ]
    if not files:
        raise RuntimeError(f"{name} ships no license file; put it in {stored}")
    texts = [
        (file.name, read(Path(distribution.locate_file(file))))
        for file in sorted(files, key=lambda file: file.name)
    ]
    for notice in NOTICE_FILES.get(name, ()):
        texts.append((Path(notice).name, read(Path(distribution.locate_file(notice)))))
    return tuple(texts)


def read(path: Path) -> str:
    lines = path.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def table(packages: list[Package]) -> str:
    widths = [
        max(len("Package"), *(len(package.name) for package in packages)),
        max(len("Version"), *(len(package.version) for package in packages)),
        max(len("License"), *(len(package.license) for package in packages)),
    ]
    rows = [("Package", "Version", "License", "Source")] + [
        (package.name, package.version, package.license, package.source) for package in packages
    ]
    return "\n".join(
        "  "
        + "  ".join(value.ljust(width) for value, width in zip(row[:3], widths, strict=True))
        + "  "
        + row[3]
        for row in rows
    )


def render(runtime: list[Package], development: list[Package]) -> str:
    parts = [
        HEADER,
        "Summary\n-------\n",
        f"Runtime dependencies ({len(runtime)}), installed with DailyScraper:\n",
        table(runtime) + "\n",
        f"Development tools ({len(development)}), used to test and check the code;"
        " not installed with DailyScraper:\n",
        table(development) + "\n",
        DOWNLOADED + "\n",
        "License texts of the runtime dependencies\n=========================================\n",
    ]
    for package in runtime:
        parts.append(
            f"{RULE}\n{package.name} {package.version}\n"
            f"License: {package.license}\nSource: {package.source}\n{RULE}\n"
        )
        for file_name, text in package.texts:
            parts.append(f"[{file_name}]\n\n{text}\n")
        if package.name == "playwright":
            parts.append(PLAYWRIGHT_BUNDLE + "\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def generate(lock_path: Path = LOCK) -> str:
    runtime, development = locked_groups(lock_path)
    return render(
        [describe(name, version, with_texts=True) for name, version in sorted(runtime.items())],
        [
            describe(name, version, with_texts=False)
            for name, version in sorted(development.items())
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or write THIRD_PARTY_LICENSES.")
    parser.add_argument("--write", action="store_true", help="rewrite the file")
    args = parser.parse_args()
    text = generate()
    if args.write:
        OUTPUT.write_bytes(text.encode("utf-8"))
        print(f"written: {OUTPUT.relative_to(ROOT)}")
        return 0
    current = OUTPUT.read_bytes().decode("utf-8") if OUTPUT.exists() else ""
    if current.replace("\r\n", "\n") != text:
        print("THIRD_PARTY_LICENSES is out of date; run:")
        print("uv run python tools/third_party_licenses.py --write")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

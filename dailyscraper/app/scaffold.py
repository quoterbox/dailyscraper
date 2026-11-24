# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""New projects and scraper packages from the templates shipped with the library.

The CLI and, later, the web interface call the same two functions.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
PROJECT_TEMPLATE = TEMPLATES / "project"
PACKAGE_TEMPLATES = TEMPLATES / "scrapers"
REPOSITORY = "https://github.com/quoterbox/dailyscraper"
NAME = re.compile(r"[a-z][a-z0-9_]*")
# Files a template keeps under other names so that tools do not treat them as
# the library's own: no hidden files in the wheel, no stray pyproject.toml.
TEMPLATE_SUFFIX = ".tmpl"
DOT_PREFIX = "dot_"


class ScaffoldError(ValueError):
    pass


def package_templates() -> tuple[str, ...]:
    return tuple(sorted(path.name for path in PACKAGE_TEMPLATES.iterdir() if path.is_dir()))


def create_project(
    target: Path, *, framework: Path | None = None, branch: str | None = None
) -> list[Path]:
    """A project folder with settings, an example package and a pyproject on dailyscraper.

    By default the project depends on the release on PyPI, starting from the
    version that created it. ``framework`` is a local dailyscraper checkout to
    depend on while editing both; ``branch`` is a branch of the public repository.
    """
    target = Path(target)
    if framework is not None and branch is not None:
        raise ScaffoldError("choose either a local framework checkout or a branch")
    if target.exists() and any(target.iterdir()):
        raise ScaffoldError(f"{target} is not empty")
    requirement, source = "dailyscraper", ""
    if framework is not None:
        path = Path(framework).resolve().as_posix()
        source = f'{{ path = "{path}", editable = true }}'
    elif branch is not None:
        source = f'{{ git = "{REPOSITORY}", branch = "{branch}" }}'
    else:
        requirement = f"dailyscraper>={version('dailyscraper')}"
    sources = f"\n\n[tool.uv.sources]\ndailyscraper = {source}" if source else ""
    name = re.sub(r"[^a-z0-9]+", "-", target.resolve().name.lower()).strip("-") or "scraping"
    values = {
        "project_name": name,
        "dailyscraper_requirement": requirement,
        "dailyscraper_sources": sources,
    }
    return _copy(PROJECT_TEMPLATE, target, values)


def create_package(scrapers: Path, name: str, template: str = "browser") -> Path:
    """A package folder ``scrapers/<name>/`` from a package template."""
    if not NAME.fullmatch(name):
        raise ScaffoldError(
            f"{name!r}: use lowercase letters, digits and _, starting with a letter"
        )
    if template not in package_templates():
        raise ScaffoldError(
            f"no template {template!r}; templates: {', '.join(package_templates())}"
        )
    target = Path(scrapers) / name
    if target.exists():
        raise ScaffoldError(f"{target} already exists")
    _copy(PACKAGE_TEMPLATES / template, target, {"scraper_name": name})
    return target


def _copy(source: Path, target: Path, values: dict[str, str]) -> list[Path]:
    written = []
    for path in sorted(source.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source)
        name = relative.name.removesuffix(TEMPLATE_SUFFIX)
        if name.startswith(DOT_PREFIX):
            name = "." + name.removeprefix(DOT_PREFIX)
        destination = target / relative.parent / name
        text = path.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
        written.append(destination)
    return written

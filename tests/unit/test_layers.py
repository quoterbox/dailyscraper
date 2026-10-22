"""Enforce the package dependency rule from roadmaps/plan.md, section 4.2."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = "dailyscraper"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / PACKAGE

# A package may import only packages of its own level or below.
LAYER_LEVELS = {
    "domain": 0,
    "schema": 1,
    "pages": 1,
    "settings": 1,
    "testing": 1,
    "transport": 2,
    "detection": 2,
    "execution": 3,
    "pipelines": 3,
    "page_scrapers": 3,
    "actions": 3,
    "storage": 4,
    "tasks": 5,
    "proxies": 5,
    "browsers": 5,
    "output": 5,
    "observability": 5,
    "dispatch": 6,
    "maintenance": 6,
    "app": 7,
}

# Edges the levels alone would allow but the architecture forbids.
FORBIDDEN_EDGES = {
    "storage": {"execution", "pipelines", "page_scrapers", "actions"},
}


def package_imports() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {name: set() for name in LAYER_LEVELS}
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if len(relative.parts) < 2:
            continue
        source = relative.parts[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                parts = module.split(".")
                if parts[0] == PACKAGE and len(parts) > 1 and parts[1] != source:
                    edges.setdefault(source, set()).add(parts[1])
    return edges


def test_every_package_has_a_layer() -> None:
    packages = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }

    assert packages == set(LAYER_LEVELS)


def test_packages_import_only_their_level_or_below() -> None:
    violations = [
        f"{source} (level {LAYER_LEVELS[source]}) -> {target} (level {LAYER_LEVELS[target]})"
        for source, targets in package_imports().items()
        for target in targets
        if LAYER_LEVELS[target] > LAYER_LEVELS[source]
        or target in FORBIDDEN_EDGES.get(source, set())
    ]

    assert violations == []

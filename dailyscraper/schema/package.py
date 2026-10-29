from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dailyscraper.pages import TemplateError, compile_detail_template, compile_list_template
from dailyscraper.schema.scraper import SCRAPER_SCHEMA_VERSION, ScraperDefinition
from dailyscraper.settings.interpolation import MissingVariableError, interpolate_env
from dailyscraper.settings.sources import deep_merge

SCRAPER_FILE = "scraper.json"
# Local changes for trial runs (fewer links, fewer pages), kept out of the packages' git.
LOCAL_SCRAPER_FILE = "scraper.local.json"
PIPELINE_FILE = "pipeline.py"
ACTIONS_FILE = "actions.py"
VERSIONED_SUFFIXES = (".json", ".py")
SHARED_DIRECTORY = "common"


class ScraperPackageError(ValueError):
    pass


@dataclass(frozen=True)
class ScraperPackage:
    root: Path
    definition: ScraperDefinition
    version: str

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def has_pipeline(self) -> bool:
        return (self.root / PIPELINE_FILE).is_file()

    @property
    def has_actions(self) -> bool:
        return (self.root / ACTIONS_FILE).is_file()


def load_scraper_package(root: Path, *, variables: Mapping[str, str]) -> ScraperPackage:
    root = Path(root)
    source = root / SCRAPER_FILE
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ScraperPackageError(f"{source} does not exist") from error
    except json.JSONDecodeError as error:
        raise ScraperPackageError(f"{source} is not valid JSON: {error}") from error
    local = root / LOCAL_SCRAPER_FILE
    if local.is_file() and isinstance(raw, dict):
        try:
            changes = json.loads(local.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ScraperPackageError(f"{local} is not valid JSON: {error}") from error
        if not isinstance(changes, dict):
            raise ScraperPackageError(f"{local} must contain a JSON object")
        deep_merge(raw, changes)

    if not isinstance(raw, dict) or raw.get("schema_version") != SCRAPER_SCHEMA_VERSION:
        raise ScraperPackageError(
            f"{source} is not a schema_version {SCRAPER_SCHEMA_VERSION} package; "
            "convert it with tools/convert_scraper.py"
        )
    try:
        definition = ScraperDefinition.model_validate(interpolate_env(raw, variables))
    except MissingVariableError as error:
        raise ScraperPackageError(f"{source}: {error.args[0]}") from error
    except ValidationError as error:
        raise ScraperPackageError(f"{source} is invalid:\n{error}") from error

    if definition.name != root.name:
        raise ScraperPackageError(
            f"{source}: name {definition.name!r} must match the package directory {root.name!r}"
        )
    _compile_templates(definition, source)
    return ScraperPackage(root=root, definition=definition, version=package_version(root))


def package_version(root: Path) -> str:
    """Content hash of the package's ``.json`` and ``.py`` files and of the shared
    ``common/`` code next to it, independent of their location.
    """
    root = Path(root)
    digest = hashlib.sha256()
    shared = root.parent / SHARED_DIRECTORY
    files = sorted(
        path
        for base in (root, shared)
        for path in base.rglob("*")
        if path.is_file() and path.suffix in VERSIONED_SUFFIXES and "__pycache__" not in path.parts
    )
    for path in files:
        name = path.relative_to(root if path.is_relative_to(root) else root.parent)
        digest.update(name.as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()[:16]


def _compile_templates(definition: ScraperDefinition, source: Path) -> None:
    try:
        if definition.pages.list is not None:
            for template in definition.pages.list.templates:
                compile_list_template(template)
        if definition.pages.detail is not None:
            for template in definition.pages.detail.templates:
                compile_detail_template(template)
    except TemplateError as error:
        raise ScraperPackageError(f"{source}: {error}") from error

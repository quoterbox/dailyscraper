from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dailyscraper.pages import TemplateError, compile_detail_template, compile_list_template
from dailyscraper.schema.scraper import SCRAPER_SCHEMA_VERSION, ScraperDefinition
from dailyscraper.settings.interpolation import MissingVariableError, interpolate_env

SCRAPER_FILE = "scraper.json"
PIPELINE_FILE = "pipeline.py"
ACTIONS_FILE = "actions.py"


class ScraperPackageError(ValueError):
    pass


@dataclass(frozen=True)
class ScraperPackage:
    root: Path
    definition: ScraperDefinition

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
    return ScraperPackage(root=root, definition=definition)


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

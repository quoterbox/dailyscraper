from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dailyscraper.settings.environment import (
    TESTING_ENVIRONMENT,
    EnvironmentPaths,
    resolve_environment_paths,
)
from dailyscraper.settings.interpolation import MissingVariableError, interpolate_env
from dailyscraper.settings.models import Settings
from dailyscraper.settings.sources import (
    CONFIG_DIRECTORY,
    SettingsError,
    deep_merge,
    read_config_directory,
    read_env_file,
)

OVERRIDE_PREFIX = "DAILYSCRAPER__"
WORKSPACE_BOUND_PATHS = ("data", "logs")


def load_variables(environment: EnvironmentPaths) -> dict[str, str]:
    return {**read_env_file(environment.env_file), **os.environ}


def load_settings(project_root: Path) -> Settings:
    root = Path(project_root).resolve()
    environment = resolve_environment_paths(root)
    variables = load_variables(environment)

    raw: dict[str, Any] = {}
    for directory in _config_directories(root, environment):
        deep_merge(raw, read_config_directory(directory))
    for path, value in _overrides(os.environ):
        _set_path(raw, path, value)

    try:
        raw = interpolate_env(raw, variables)
        settings = Settings.model_validate(
            {**raw, "environment": environment.name, "project_root": root}
        )
    except MissingVariableError as error:
        raise SettingsError(error.args[0]) from error
    except ValidationError as error:
        raise SettingsError(f"invalid settings for {environment.name!r}:\n{error}") from error

    settings = _resolve_paths(settings, root)
    if environment.name == TESTING_ENVIRONMENT:
        _require_inside_workspace(settings, environment.test_workspace)
    return settings


def _config_directories(root: Path, environment: EnvironmentPaths) -> list[Path]:
    directories = [root / CONFIG_DIRECTORY / environment.name]
    if not directories[0].is_dir():
        raise SettingsError(f"configuration directory {directories[0]} does not exist")
    if environment.test_config_root is not None:
        directories.append(environment.test_config_root)
    return directories


def _overrides(environ: Mapping[str, str]) -> list[tuple[list[str], Any]]:
    overrides = []
    for name, value in sorted(environ.items()):
        if not name.startswith(OVERRIDE_PREFIX):
            continue
        path = [part.lower() for part in name[len(OVERRIDE_PREFIX) :].split("__")]
        if not all(path):
            raise SettingsError(f"malformed settings override variable {name}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        overrides.append((path, parsed))
    return overrides


def _set_path(target: dict[str, Any], path: list[str], value: Any) -> None:
    node = target
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[path[-1]] = value


def _resolve_paths(settings: Settings, root: Path) -> Settings:
    def absolute(path: Path) -> Path:
        return (path if path.is_absolute() else root / path).resolve()

    paths = {name: absolute(path) for name, path in settings.paths.model_dump().items()}
    storage = settings.storage
    if storage.database is not None:
        storage = storage.model_copy(update={"database": absolute(storage.database)})
    return settings.model_copy(
        update={"paths": settings.paths.model_copy(update=paths), "storage": storage}
    )


def _require_inside_workspace(settings: Settings, workspace: Path | None) -> None:
    if workspace is None:
        raise SettingsError("the testing environment requires a test workspace")
    outside = [
        f"paths.{name}={getattr(settings.paths, name)}"
        for name in WORKSPACE_BOUND_PATHS
        if not getattr(settings.paths, name).is_relative_to(workspace)
    ]
    if not settings.database_path.is_relative_to(workspace):
        outside.append(f"storage.database={settings.database_path}")
    if outside:
        raise SettingsError(f"testing paths must be inside {workspace}: {', '.join(outside)}")

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

CONFIG_DIRECTORY = "config"
LOCAL_SUFFIX = ".local.json"


class SettingsError(ValueError):
    pass


def read_config_directory(directory: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for path in config_files(directory):
        deep_merge(raw, read_json(path))
    return raw


def config_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.json"))
    base = [path for path in files if not path.name.endswith(LOCAL_SUFFIX)]
    local = [path for path in files if path.name.endswith(LOCAL_SUFFIX)]
    return base + local


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SettingsError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain a JSON object")
    return data


def deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value


def read_env_file(path: Path) -> dict[str, str]:
    return {name: value for name, value in dotenv_values(path).items() if value is not None}

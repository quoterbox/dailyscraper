from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ENV_REFERENCE = re.compile(r"\$\{env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")


class MissingVariableError(KeyError):
    pass


def interpolate_env(data: Any, variables: Mapping[str, str]) -> Any:
    """Replace ``${env:NAME}`` in every string of a JSON-like structure."""

    if isinstance(data, str):
        return _ENV_REFERENCE.sub(lambda match: _lookup(match["name"], variables), data)
    if isinstance(data, dict):
        return {key: interpolate_env(value, variables) for key, value in data.items()}
    if isinstance(data, list):
        return [interpolate_env(value, variables) for value in data]
    return data


def _lookup(name: str, variables: Mapping[str, str]) -> str:
    if name not in variables:
        raise MissingVariableError(f"environment variable {name} is not set")
    return variables[name]

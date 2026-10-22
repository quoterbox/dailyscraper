from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENVIRONMENT_VARIABLE = "DAILYSCRAPER_ENV"
DEVELOPMENT_ENVIRONMENT = "development"
TESTING_ENVIRONMENT = "testing"
DEVELOPMENT_ENV_FILE = ".env"
TEST_ENV_FILE = ".test.env"
TEST_WORKSPACE_VARIABLE = "DAILYSCRAPER_TEST_WORKSPACE"
TEST_CONFIG_ROOT_VARIABLE = "DAILYSCRAPER_TEST_CONFIG_ROOT"
TEST_SESSION_ID_VARIABLE = "DAILYSCRAPER_TEST_SESSION_ID"

PROTECTED_DEVELOPMENT_PATHS = (
    Path("config") / DEVELOPMENT_ENVIRONMENT,
    Path("data"),
    Path("logs"),
    Path("scrapers"),
)


@dataclass(frozen=True)
class EnvironmentPaths:
    name: str
    env_file: Path
    test_workspace: Path | None = None
    test_config_root: Path | None = None


def current_environment() -> str:
    return str(os.getenv(ENVIRONMENT_VARIABLE) or DEVELOPMENT_ENVIRONMENT).strip().lower()


def resolve_environment_paths(project_root: Path) -> EnvironmentPaths:
    root = Path(project_root).resolve()
    environment = current_environment()
    if environment != TESTING_ENVIRONMENT:
        return EnvironmentPaths(name=environment, env_file=root / DEVELOPMENT_ENV_FILE)

    workspace = require_testing_workspace(root)
    config_root_value = str(os.getenv(TEST_CONFIG_ROOT_VARIABLE) or "").strip()
    config_root = (
        validate_testing_config_root(
            Path(config_root_value),
            project_root=root,
            workspace=workspace,
        )
        if config_root_value
        else None
    )
    return EnvironmentPaths(
        name=environment,
        env_file=root / TEST_ENV_FILE,
        test_workspace=workspace,
        test_config_root=config_root,
    )


def require_testing_workspace(project_root: Path) -> Path:
    raw_value = str(os.getenv(TEST_WORKSPACE_VARIABLE) or "").strip()
    if not raw_value:
        raise RuntimeError(
            f"{TEST_WORKSPACE_VARIABLE} is required when "
            f"{ENVIRONMENT_VARIABLE}={TESTING_ENVIRONMENT}."
        )
    workspace = Path(raw_value).expanduser().resolve()
    validate_testing_workspace(workspace, project_root=project_root)
    return workspace


def validate_testing_workspace(workspace: Path, *, project_root: Path) -> Path:
    resolved_workspace = Path(workspace).expanduser().resolve()
    resolved_project = Path(project_root).resolve()

    if resolved_workspace == Path(resolved_workspace.anchor):
        raise RuntimeError("Testing workspace cannot be a filesystem root.")
    if resolved_workspace == resolved_project:
        raise RuntimeError("Testing workspace cannot be the project root.")
    for protected in PROTECTED_DEVELOPMENT_PATHS:
        if resolved_workspace.is_relative_to((resolved_project / protected).resolve()):
            raise RuntimeError(
                f"Testing workspace overlaps a protected development path: {resolved_workspace}"
            )
    return resolved_workspace


def validate_testing_config_root(
    config_root: Path,
    *,
    project_root: Path,
    workspace: Path,
) -> Path:
    resolved = Path(config_root).expanduser().resolve()
    development_root = (Path(project_root) / "config" / DEVELOPMENT_ENVIRONMENT).resolve()
    if resolved.is_relative_to(development_root):
        raise RuntimeError(f"Testing config root cannot use config/development: {resolved}")
    if not resolved.is_relative_to(Path(workspace).resolve()):
        raise RuntimeError(
            f"Testing config overrides must be inside {TEST_WORKSPACE_VARIABLE}: {resolved}"
        )
    return resolved

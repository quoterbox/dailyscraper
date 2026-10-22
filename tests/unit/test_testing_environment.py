from __future__ import annotations

import os
from pathlib import Path

import pytest

from dailyscraper.settings.environment import (
    ENVIRONMENT_VARIABLE,
    TEST_CONFIG_ROOT_VARIABLE,
    TEST_WORKSPACE_VARIABLE,
    TESTING_ENVIRONMENT,
    resolve_environment_paths,
    validate_testing_config_root,
    validate_testing_workspace,
)
from dailyscraper.testing.fault_injection import FAULT_PLAN_VARIABLE


def test_pytest_session_runs_in_isolated_testing_environment(project_root: Path) -> None:
    workspace = Path(os.environ[TEST_WORKSPACE_VARIABLE]).resolve()

    assert os.environ[ENVIRONMENT_VARIABLE] == TESTING_ENVIRONMENT
    assert FAULT_PLAN_VARIABLE not in os.environ
    assert workspace.is_relative_to(project_root / ".test-artifacts")


@pytest.mark.parametrize(
    "protected",
    [Path("data"), Path("logs"), Path("scrapers"), Path("config") / "development"],
)
def test_testing_workspace_rejects_development_paths(project_root: Path, protected: Path) -> None:
    with pytest.raises(RuntimeError, match="protected development path"):
        validate_testing_workspace(project_root / protected / "pytest", project_root=project_root)


@pytest.mark.parametrize("workspace", ["root", "project"])
def test_testing_workspace_rejects_root_and_project(project_root: Path, workspace: str) -> None:
    path = Path(project_root.anchor) if workspace == "root" else project_root

    with pytest.raises(RuntimeError, match="cannot be"):
        validate_testing_workspace(path, project_root=project_root)


def test_testing_config_override_must_stay_inside_workspace(
    tmp_path: Path,
    project_root: Path,
) -> None:
    with pytest.raises(RuntimeError, match="inside DAILYSCRAPER_TEST_WORKSPACE"):
        validate_testing_config_root(
            tmp_path / "outside" / "config",
            project_root=project_root,
            workspace=tmp_path / "workspace",
        )


def test_testing_environment_paths_use_session_workspace(
    tmp_path: Path,
    project_root: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    config_root = workspace / "config"
    monkeypatch.setenv(TEST_WORKSPACE_VARIABLE, str(workspace))
    monkeypatch.setenv(TEST_CONFIG_ROOT_VARIABLE, str(config_root))

    paths = resolve_environment_paths(project_root)

    assert paths.name == TESTING_ENVIRONMENT
    assert paths.env_file == project_root / ".test.env"
    assert paths.test_workspace == workspace.resolve()
    assert paths.test_config_root == config_root.resolve()


def test_development_environment_does_not_require_workspace(
    project_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "development")
    monkeypatch.delenv(TEST_WORKSPACE_VARIABLE, raising=False)

    paths = resolve_environment_paths(project_root)

    assert paths.name == "development"
    assert paths.test_workspace is None

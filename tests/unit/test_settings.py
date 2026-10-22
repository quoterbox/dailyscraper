from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dailyscraper.settings import SettingsError, load_settings
from dailyscraper.settings.environment import TEST_CONFIG_ROOT_VARIABLE, TEST_WORKSPACE_VARIABLE

WORKSPACE_PATHS = {
    "paths": {
        "data": "${env:DAILYSCRAPER_TEST_WORKSPACE}/data",
        "logs": "${env:DAILYSCRAPER_TEST_WORKSPACE}/logs",
        "scrapers": "tests/scrapers",
    }
}


@pytest.fixture
def workspace() -> Path:
    return Path(os.environ[TEST_WORKSPACE_VARIABLE]).resolve()


def make_project(root: Path, files: dict[str, object]) -> Path:
    config = root / "config" / "testing"
    config.mkdir(parents=True)
    for name, content in files.items():
        text = content if isinstance(content, str) else json.dumps(content)
        (config / name).write_text(text, encoding="utf-8")
    return root


def test_project_testing_settings_stay_inside_the_session_workspace(
    project_root: Path, workspace: Path
) -> None:
    settings = load_settings(project_root)

    assert settings.environment == "testing"
    assert settings.paths.data == workspace / "data"
    assert settings.paths.logs == workspace / "logs"
    assert settings.paths.scrapers == project_root / "tests" / "scrapers"
    assert settings.scrapers == ()


def test_local_file_and_environment_override_in_order(
    tmp_path: Path, workspace: Path, monkeypatch
) -> None:
    project = make_project(
        tmp_path,
        {
            "paths.json": WORKSPACE_PATHS,
            "paths.local.json": {"paths": {"data": "${env:DAILYSCRAPER_TEST_WORKSPACE}/local"}},
            "scrapers.json": {"scrapers": [{"name": "shop"}]},
        },
    )
    monkeypatch.setenv("DAILYSCRAPER__PATHS__LOGS", str(workspace / "override-logs"))
    monkeypatch.setenv("DAILYSCRAPER__SCRAPERS", '[{"name": "other", "aliases": ["o"]}]')

    settings = load_settings(project)

    assert settings.paths.data == workspace / "local"
    assert settings.paths.logs == workspace / "override-logs"
    assert [(item.name, item.aliases) for item in settings.scrapers] == [("other", ("o",))]


def test_test_config_root_overlays_project_config(
    tmp_path: Path, workspace: Path, monkeypatch
) -> None:
    project = make_project(tmp_path / "project", {"paths.json": WORKSPACE_PATHS})
    overlay = workspace / "overlay-config"
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "scrapers.json").write_text(
        json.dumps({"scrapers": [{"name": "controlled_catalog"}]}), encoding="utf-8"
    )
    monkeypatch.setenv(TEST_CONFIG_ROOT_VARIABLE, str(overlay))

    settings = load_settings(project)

    assert [item.name for item in settings.scrapers] == ["controlled_catalog"]


def test_env_file_supplies_references_and_process_environment_wins(
    tmp_path: Path, monkeypatch
) -> None:
    project = make_project(
        tmp_path,
        {
            "paths.json": {
                "paths": {
                    "data": "${env:DAILYSCRAPER_TEST_WORKSPACE}/${env:DATA_DIR}",
                    "logs": "${env:DAILYSCRAPER_TEST_WORKSPACE}/${env:LOGS_DIR}",
                    "scrapers": "scrapers",
                }
            }
        },
    )
    (project / ".test.env").write_text("DATA_DIR=from-file\nLOGS_DIR=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LOGS_DIR", "from-process")

    settings = load_settings(project)

    assert settings.paths.data.name == "from-file"
    assert settings.paths.logs.name == "from-process"


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"paths.json": "{"}, "not valid JSON"),
        ({"paths.json": "[]"}, "must contain a JSON object"),
        (
            {"paths.json": {"paths": {**WORKSPACE_PATHS["paths"], "data": "${env:NOT_SET_X}"}}},
            "NOT_SET_X",
        ),
        ({"paths.json": {**WORKSPACE_PATHS, "browser": {}}}, "Extra inputs"),
        ({"paths.json": {"paths": {**WORKSPACE_PATHS["paths"], "data": "data"}}}, "must be inside"),
    ],
)
def test_invalid_settings_are_rejected(tmp_path: Path, files: dict, message: str) -> None:
    project = make_project(tmp_path, files)

    with pytest.raises(SettingsError, match=message):
        load_settings(project)


def test_missing_environment_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="does not exist"):
        load_settings(tmp_path)


def test_settings_are_immutable(project_root: Path) -> None:
    settings = load_settings(project_root)

    with pytest.raises(ValueError, match="frozen"):
        settings.paths.data = Path("elsewhere")

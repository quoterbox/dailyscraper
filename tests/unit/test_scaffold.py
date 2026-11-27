# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from dailyscraper.actions import ScraperActions
from dailyscraper.app.scaffold import ScaffoldError, create_package, create_project
from dailyscraper.execution.loader import load_package_class
from dailyscraper.proxies.sources import build_pool
from dailyscraper.schema import load_scraper_package
from dailyscraper.settings.models import ProxiesSettings


def test_a_project_has_settings_an_example_and_a_pyproject_on_the_framework(
    tmp_path: Path,
) -> None:
    project = tmp_path / "My Scraping"

    files = {path.relative_to(project).as_posix() for path in create_project(project)}

    assert {
        ".env.example",
        ".gitignore",
        "README.md",
        "pyproject.toml",
        "config/development/paths.json",
        "config/development/scrapers.json",
        "config/production/browser.json",
        "scrapers/example_catalog/scraper.json",
    } <= files
    pyproject = tomllib.loads((project / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["name"] == "my-scraping"
    assert pyproject["project"]["dependencies"] == [f"dailyscraper>={version('dailyscraper')}"]
    assert "sources" not in pyproject["tool"]["uv"]
    assert "data/" in (project / ".gitignore").read_text("utf-8").splitlines()
    scrapers = json.loads((project / "config/development/scrapers.json").read_text("utf-8"))
    assert [selection["name"] for selection in scrapers["scrapers"]] == ["example_catalog"]


def test_a_project_may_depend_on_a_local_checkout(tmp_path: Path, project_root: Path) -> None:
    create_project(tmp_path / "work", framework=project_root)

    pyproject = tomllib.loads((tmp_path / "work" / "pyproject.toml").read_text("utf-8"))
    source = pyproject["tool"]["uv"]["sources"]["dailyscraper"]
    assert pyproject["project"]["dependencies"] == ["dailyscraper"]
    assert source == {"path": project_root.as_posix(), "editable": True}


def test_a_project_may_depend_on_a_repository_branch(tmp_path: Path) -> None:
    create_project(tmp_path / "work", branch="dev")

    pyproject = tomllib.loads((tmp_path / "work" / "pyproject.toml").read_text("utf-8"))
    source = pyproject["tool"]["uv"]["sources"]["dailyscraper"]
    assert pyproject["project"]["dependencies"] == ["dailyscraper"]
    assert source == {"git": "https://github.com/quoterbox/dailyscraper", "branch": "dev"}


def test_a_project_takes_one_framework_source(tmp_path: Path, project_root: Path) -> None:
    with pytest.raises(ScaffoldError, match="either"):
        create_project(tmp_path / "work", framework=project_root, branch="dev")


def test_a_project_is_not_created_over_existing_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(ScaffoldError, match="not empty"):
        create_project(tmp_path)


def test_the_example_is_the_controlled_catalog(tmp_path: Path, project_root: Path) -> None:
    create_project(tmp_path / "work")
    example = json.loads(
        (tmp_path / "work/scrapers/example_catalog/scraper.json").read_text("utf-8")
    )
    controlled = json.loads(
        (project_root / "tests/scrapers/controlled_catalog/scraper.json").read_text("utf-8")
    )

    assert example == {**controlled, "name": "example_catalog"}
    assert load_scraper_package(tmp_path / "work/scrapers/example_catalog", variables={})


def test_the_proxy_example_is_valid_and_not_loaded(tmp_path: Path) -> None:
    create_project(tmp_path / "work")
    example = json.loads((tmp_path / "work/config/examples/proxies.json").read_text("utf-8"))

    for source in ("static", "gateway"):
        proxies = ProxiesSettings.model_validate({**example["proxies"], "source": source})
        assert not build_pool(proxies).empty
    settings = ProxiesSettings.model_validate(
        json.loads((tmp_path / "work/config/development/proxies.json").read_text("utf-8"))[
            "proxies"
        ]
    )
    assert build_pool(settings).empty


def test_a_new_package_loads_with_its_actions(tmp_path: Path) -> None:
    target = create_package(tmp_path, "my_shop")

    package = load_scraper_package(target, variables={})
    actions = load_package_class(package, "actions", "Actions", ScraperActions)

    assert package.name == "my_shop"
    assert actions is not ScraperActions and issubclass(actions, ScraperActions)
    assert "{{" not in (target / "actions.py").read_text("utf-8")


@pytest.mark.parametrize(
    ("name", "template", "message"),
    [
        ("My-Shop", "browser", "lowercase"),
        ("my_shop", "nonexistent", "no template"),
    ],
)
def test_bad_package_requests_are_refused(
    tmp_path: Path, name: str, template: str, message: str
) -> None:
    with pytest.raises(ScaffoldError, match=message):
        create_package(tmp_path, name, template)


def test_an_existing_package_is_not_overwritten(tmp_path: Path) -> None:
    create_package(tmp_path, "my_shop")

    with pytest.raises(ScaffoldError, match="already exists"):
        create_package(tmp_path, "my_shop")

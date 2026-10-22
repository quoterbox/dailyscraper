from __future__ import annotations

import json
from pathlib import Path

import pytest

from dailyscraper.schema import ScraperPackageError, load_scraper_package


def write_package(root: Path, name: str, definition: dict) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "scraper.json").write_text(json.dumps(definition), encoding="utf-8")
    return package


def minimal(name: str = "shop", **overrides) -> dict:
    return {
        "schema_version": 3,
        "name": name,
        "website": "https://shop.test",
        "pages": {
            "detail": {"templates": [{"name": "detail", "fields": {"title": {"css": "h1"}}}]}
        },
        **overrides,
    }


@pytest.mark.parametrize("name", ["controlled_catalog", "controlled_details"])
def test_controlled_packages_load(project_root: Path, name: str) -> None:
    package = load_scraper_package(project_root / "tests" / "scrapers" / name, variables={})

    assert package.name == name
    assert package.definition.website == "https://dailyscraper.com"
    assert package.has_pipeline is False
    assert package.has_actions is False


def test_package_secrets_come_from_env_references(tmp_path: Path) -> None:
    definition = minimal(
        pages={
            "auth": {
                "credentials": {"login": "${env:SHOP_LOGIN}", "password": "${env:SHOP_PASSWORD}"},
                "templates": [
                    {
                        "name": "auth",
                        "login_field": {"css": "#login"},
                        "password_field": {"css": "#password"},
                        "submit": {"css": "button"},
                        "authorized": {"css": ".account"},
                    }
                ],
            },
            "detail": {"templates": [{"name": "detail", "fields": {"title": {"css": "h1"}}}]},
        }
    )
    root = write_package(tmp_path, "shop", definition)

    package = load_scraper_package(
        root, variables={"SHOP_LOGIN": "user", "SHOP_PASSWORD": "secret"}
    )

    credentials = package.definition.pages.auth.credentials
    assert (credentials.login, credentials.password) == ("user", "secret")
    assert "secret" not in repr(credentials)
    with pytest.raises(ScraperPackageError, match="SHOP_LOGIN is not set"):
        load_scraper_package(root, variables={})


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({"shop": {"website": "https://shop.test"}}, "not a schema_version 3 package"),
        (minimal(name="other"), "must match the package directory"),
        (minimal(pages={}), "at least a list or a detail page"),
        (
            minimal(links=[{"id": "main", "type": "list", "url_template": "https://x/{page}"}]),
            "needs urls",
        ),
        (
            minimal(
                links=[
                    {"id": "main", "urls": ["https://x/a"]},
                    {"id": "main", "urls": ["https://x/b"]},
                ]
            ),
            "ids must be unique",
        ),
        (
            minimal(
                pages={
                    "list": {
                        "templates": [
                            {"name": "broken", "first_item": {"fields": {"a": {"css": ".a"}}}}
                        ]
                    }
                }
            ),
            "list template 'broken'",
        ),
        (minimal(unknown_section={}), "Extra inputs"),
    ],
)
def test_invalid_packages_are_rejected(tmp_path: Path, definition: dict, message: str) -> None:
    root = write_package(tmp_path, "shop", definition)

    with pytest.raises(ScraperPackageError, match=message):
        load_scraper_package(root, variables={})


def test_missing_and_malformed_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ScraperPackageError, match="does not exist"):
        load_scraper_package(tmp_path / "shop", variables={})

    package = tmp_path / "broken"
    package.mkdir()
    (package / "scraper.json").write_text("{", encoding="utf-8")
    with pytest.raises(ScraperPackageError, match="not valid JSON"):
        load_scraper_package(package, variables={})

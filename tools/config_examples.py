# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

"""Writes ``config/examples/`` of the project template: every setting, with its default or a
sample value where the default is empty.

    uv run python -m tools.config_examples
"""

from __future__ import annotations

import json
from pathlib import Path

from dailyscraper.settings.models import Settings

EXAMPLES = Path(__file__).resolve().parents[1] / "dailyscraper/templates/project/config/examples"

FILES: dict[str, tuple[str, ...]] = {
    "paths.json": ("project_id", "paths"),
    "scrapers.json": ("scrapers", "groups"),
    "browser.json": ("browser",),
    "runtime.json": ("runtime",),
    "proxies.json": ("proxies", "proxy_health", "proxy_check"),
    "storage.json": ("storage",),
    "output.json": ("output",),
    "interventions.json": ("interventions",),
    "notifications.json": ("notifications",),
}

SAMPLE = {
    "paths": {"data": "data", "logs": "logs", "scrapers": "scrapers"},
    "scrapers": [
        {
            "name": "example_catalog",
            "aliases": ["catalog"],
            "proxy_names": ["proxy_1_ru", "proxy_2_ru"],
            "max_parallel": 2,
        }
    ],
    "groups": {"daily": ["example_catalog"]},
    "browser": {
        "args": [
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-blink-features=AutomationControlled",
        ],
        "resource_blocking": {
            "block_url_patterns": ["*://*.doubleclick.net/*"],
            "allow_url_patterns": ["*://shop.example/static/*"],
        },
    },
    "runtime": {
        "limits": [
            {
                "name": "shop_browsers",
                "scope": ["site"],
                "limit": 2,
                "match": {"site": "shop.example"},
            }
        ]
    },
    "proxies": {
        "source": "static",
        "static": {
            "default_auth": {"user": "${env:PROXY_USER}", "password": "${env:PROXY_PASSWORD}"},
            "items": [
                {"name": "proxy_1_ru", "host": "203.0.113.10", "port": 8000, "country_code": "RU"},
                {
                    "name": "proxy_2_ru",
                    "host": "203.0.113.11",
                    "port": 8000,
                    "user": "${env:PROXY_2_RU_USER}",
                    "password": "${env:PROXY_2_RU_PASSWORD}",
                    "country_code": "RU",
                },
                {
                    "name": "proxy_1_shop",
                    "host": "203.0.113.12",
                    "port": 8000,
                    "country_code": "RU",
                    "allowed_sites": ["shop.example"],
                    "site_locked": True,
                },
            ],
        },
        "gateway": {
            "host": "gate.example.com",
            "port": 10000,
            "port_range": [10000, 10015],
            "sessions": 16,
            "user": "${env:GATEWAY_USER}",
            "password": "${env:GATEWAY_PASSWORD}",
            "session_template": "{user}_c_RU_s_{session}_ttl_30m",
            "country_code": "RU",
        },
        "api": {
            "url_env": "PROXY_API_URL",
            "token_env": "PROXY_API_TOKEN",
            "items_field": "data.items",
            "field_map": {
                "name": "title",
                "host": "ip",
                "port": "port",
                "user": "login",
                "password": "password",
                "country_code": "geo.country",
            },
            "fallback_to_static": True,
        },
    },
    "output": {
        "deliveries": [{"name": "shared_folder", "type": "folder", "path": "exports/{date}"}]
    },
    "notifications": {
        "channels": [
            {"type": "telegram"},
            {
                "type": "email",
                "host": "smtp.example.com",
                "username_env": "SMTP_USER",
                "password_env": "SMTP_PASSWORD",
                "from_address": "scraper@example.com",
                "recipients": ["team@example.com"],
            },
        ]
    },
}


def render() -> dict[str, str]:
    settings = Settings.model_validate({"environment": "example", "project_root": ".", **SAMPLE})
    dumped = settings.model_dump(mode="json", exclude={"environment", "project_root"})
    placed = {section for sections in FILES.values() for section in sections}
    if missing := set(dumped) - placed:
        raise ValueError(f"settings sections without an example file: {sorted(missing)}")
    return {
        name: json.dumps({section: dumped[section] for section in sections}, indent=4) + "\n"
        for name, sections in FILES.items()
    }


def main() -> None:
    files = render()
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for stale in EXAMPLES.glob("*.json"):
        if stale.name not in files:
            stale.unlink()
    for name, text in files.items():
        (EXAMPLES / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {len(files)} files to {EXAMPLES}")


if __name__ == "__main__":
    main()

"""Settings and store for runs through real slot processes."""

from __future__ import annotations

from pathlib import Path

import pytest

from dailyscraper.settings import load_settings
from dailyscraper.settings.models import OutputSettings, RuntimeSettings, StorageSettings
from dailyscraper.storage import open_store


@pytest.fixture
def settings(project_root: Path, scenario_workspace: Path):
    loaded = load_settings(project_root)
    return loaded.model_copy(
        update={
            "paths": loaded.paths.model_copy(
                update={
                    "data": scenario_workspace / "data",
                    "scrapers": scenario_workspace / "scrapers",
                }
            ),
            "storage": StorageSettings(batch_rows=2, fsync=False),
            "output": OutputSettings(formats=("csv",)),
            "runtime": RuntimeSettings(
                max_slots=4,
                max_browsers_per_site_proxy=10,
                memory_reserve_mb=0,
                slot_memory_mb=1,
                items_per_task=2,
                retry_delay_seconds=0,
                poll_interval_seconds=0.1,
                stop_grace_seconds=5,
            ),
        }
    )


@pytest.fixture
def store(settings):
    opened = open_store("sqlite", settings.database_path)
    yield opened
    opened.close()

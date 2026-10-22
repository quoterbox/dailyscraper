from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SettingsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PathsSettings(SettingsModel):
    data: Path
    logs: Path
    scrapers: Path


class ScraperSelection(SettingsModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    aliases: tuple[str, ...] = ()
    proxy_names: tuple[str, ...] = ()


class Settings(SettingsModel):
    environment: str
    project_root: Path
    paths: PathsSettings
    scrapers: tuple[ScraperSelection, ...] = ()

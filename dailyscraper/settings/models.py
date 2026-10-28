from __future__ import annotations

from pathlib import Path
from typing import Literal

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


class Viewport(SettingsModel):
    width: int = Field(1920, gt=0)
    height: int = Field(1080, gt=0)


class PlaywrightSettings(SettingsModel):
    headless: bool = True
    channel: str | None = None
    slow_mo_ms: int = Field(0, ge=0)
    navigation_timeout_ms: int = Field(60_000, gt=0)
    default_timeout_ms: int = Field(10_000, gt=0)
    text_timeout_ms: int = Field(1_500, gt=0)
    attribute_timeout_ms: int = Field(1_500, gt=0)
    action_timeout_ms: int = Field(5_000, gt=0)
    typing_delay_ms: int = Field(30, ge=0)
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    viewport: Viewport | None = Viewport()
    locale: str | None = None
    timezone_id: str | None = None


class StealthSettings(SettingsModel):
    enabled: bool = False


class BrowserSettings(SettingsModel):
    args: tuple[str, ...] = ()
    user_agent: str | None = None
    open_attempts: int = Field(2, ge=1)
    playwright: PlaywrightSettings = PlaywrightSettings()
    stealth: StealthSettings = StealthSettings()


class StorageSettings(SettingsModel):
    backend: Literal["sqlite"] = "sqlite"
    database: Path | None = Field(None, description="Defaults to <paths.data>/dailyscraper.sqlite")
    busy_timeout_ms: int = Field(30_000, gt=0)
    batch_rows: int = Field(100, ge=1)
    batch_seconds: float = Field(5.0, gt=0)
    partition_max_rows: int = Field(100_000, ge=1)
    partition_max_bytes: int = Field(128 * 1024 * 1024, ge=1)
    fsync: bool = True


OutputFormat = Literal["csv", "json", "xlsx", "xml"]


class DeliverySettings(SettingsModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    type: Literal["folder"]
    path: str = Field(min_length=1, description="May contain {run_id}, {project}, {date}")
    formats: tuple[OutputFormat, ...] | None = None
    archive: bool = False
    when: Literal["success", "always"] = Field(
        "success", description="success: the run completed, possibly with failed items"
    )
    attempts: int = Field(3, ge=1)


class OutputSettings(SettingsModel):
    formats: tuple[OutputFormat, ...] = ("csv",)
    archive: bool = False
    deliveries: tuple[DeliverySettings, ...] = ()


class Settings(SettingsModel):
    environment: str
    project_root: Path
    project_id: str = Field("default", pattern=r"^[A-Za-z0-9_.-]+$")
    paths: PathsSettings
    browser: BrowserSettings = BrowserSettings()
    storage: StorageSettings = StorageSettings()
    output: OutputSettings = OutputSettings()
    scrapers: tuple[ScraperSelection, ...] = ()

    @property
    def database_path(self) -> Path:
        return self.storage.database or self.paths.data / "dailyscraper.sqlite"

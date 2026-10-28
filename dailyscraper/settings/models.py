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


class Settings(SettingsModel):
    environment: str
    project_root: Path
    paths: PathsSettings
    browser: BrowserSettings = BrowserSettings()
    scrapers: tuple[ScraperSelection, ...] = ()

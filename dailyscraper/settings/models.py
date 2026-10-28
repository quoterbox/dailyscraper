from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dailyscraper.domain.limits import Dimension, LimitRule


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
    max_parallel: int | None = Field(None, ge=1)


LimitDimension = Literal["host", "site", "proxy", "scraper", "run", "project", "kind"]


class LimitSettings(SettingsModel):
    """A session limit over a combination of dimensions; see ``domain.limits.LimitRule``."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    scope: tuple[LimitDimension, ...] = Field(min_length=1)
    limit: int = Field(ge=1)
    match: dict[LimitDimension, str] = Field(default_factory=dict)


class RuntimeSettings(SettingsModel):
    host_id: str | None = Field(None, description="Defaults to the machine's host name")
    max_slots: int = Field(4, ge=1, description="Browser slots of this machine for all runs")
    max_parallel_per_scraper: int = Field(1, ge=1)
    max_browsers_per_site_proxy: int = Field(
        1, ge=1, description="Browsers of one site through one proxy (or the direct connection)"
    )
    slot_memory_mb: int = Field(400, ge=1)
    memory_reserve_mb: int = Field(1024, ge=0)
    items_per_task: int = Field(50, ge=1)
    task_attempts: int = Field(3, ge=1)
    retry_delay_seconds: float = Field(5.0, ge=0)
    retry_max_delay_seconds: float = Field(120.0, ge=0)
    stall_timeout_seconds: float = Field(600.0, gt=0)
    task_deadline_seconds: float = Field(7200.0, gt=0)
    slot_start_timeout_seconds: float = Field(120.0, gt=0)
    stop_grace_seconds: float = Field(30.0, ge=0)
    commit_ack_timeout_seconds: float = Field(60.0, gt=0)
    lease_ttl_seconds: float = Field(60.0, gt=0)
    poll_interval_seconds: float = Field(0.5, gt=0)
    snapshots_per_slot: int = Field(20, ge=0)
    limits: tuple[LimitSettings, ...] = ()


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
    runtime: RuntimeSettings = RuntimeSettings()
    scrapers: tuple[ScraperSelection, ...] = ()

    def limit_rules(self) -> list[LimitRule]:
        """Built-in limits from ``max_slots``, ``max_parallel`` and
        ``max_browsers_per_site_proxy``, then ``runtime.limits``."""
        runtime = self.runtime
        rules = [
            LimitRule(
                "host_browsers",
                (Dimension.HOST, Dimension.KIND),
                runtime.max_slots,
                {Dimension.KIND: "browser"},
            ),
            LimitRule("scraper_parallel", (Dimension.SCRAPER,), runtime.max_parallel_per_scraper),
            LimitRule(
                "site_proxy",
                (Dimension.SITE, Dimension.PROXY),
                runtime.max_browsers_per_site_proxy,
            ),
        ]
        rules += [
            LimitRule(
                "scraper_parallel",
                (Dimension.SCRAPER,),
                selection.max_parallel,
                {Dimension.SCRAPER: selection.name},
            )
            for selection in self.scrapers
            if selection.max_parallel is not None
        ]
        rules += [
            LimitRule(
                limit.name,
                tuple(Dimension(name) for name in limit.scope),
                limit.limit,
                {Dimension(name): value for name, value in limit.match.items()},
            )
            for limit in runtime.limits
        ]
        return rules

    @property
    def database_path(self) -> Path:
        return self.storage.database or self.paths.data / "dailyscraper.sqlite"

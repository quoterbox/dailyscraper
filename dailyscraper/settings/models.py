from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    slot_max_tasks: int = Field(
        0, ge=0, description="Restart the session after so many tasks; 0: never"
    )
    slot_max_age_seconds: float = Field(
        0, ge=0, description="Restart the session after this age; 0: never"
    )
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


class ResourceBlockingSettings(SettingsModel):
    """Heavy resources no selector reads; scripts and styles are never blocked by default."""

    enabled: bool = True
    resource_types: tuple[str, ...] = ("image", "media", "font")
    block_url_patterns: tuple[str, ...] = ()
    allow_url_patterns: tuple[str, ...] = ()


class ProfileSettings(SettingsModel):
    reset_on_identity_change: bool = Field(
        True, description="Start a clean profile when the proxy's exit identity changes"
    )


class Mv3Settings(SettingsModel):
    readiness_timeout_ms: int = Field(10_000, gt=0)
    settle_ms: int = Field(300, ge=0)


class BrowserSettings(SettingsModel):
    args: tuple[str, ...] = ()
    user_agent: str | None = None
    open_attempts: int = Field(2, ge=1)
    isolation: Literal["ephemeral", "persistent"] = Field(
        "ephemeral",
        description="ephemeral: clean browser, native proxy auth; persistent: profile per "
        "site and proxy, proxy auth through an MV3 extension",
    )
    playwright: PlaywrightSettings = PlaywrightSettings()
    stealth: StealthSettings = StealthSettings()
    resource_blocking: ResourceBlockingSettings = ResourceBlockingSettings()
    profiles: ProfileSettings = ProfileSettings()
    mv3: Mv3Settings = Mv3Settings()


ProxyScheme = Literal["http", "https", "socks5"]
ProxyName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+$")]


class ProxyAuth(SettingsModel):
    user: str | None = None
    password: str | None = Field(None, repr=False)


class StaticProxy(SettingsModel):
    name: ProxyName
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    scheme: ProxyScheme = "http"
    user: str | None = None
    password: str | None = Field(None, repr=False)
    country_code: str | None = None


class StaticSource(SettingsModel):
    items: tuple[StaticProxy, ...] = ()
    default_auth: ProxyAuth = ProxyAuth()

    @model_validator(mode="after")
    def _unique_names(self) -> StaticSource:
        names = [item.name for item in self.items]
        if len(names) != len(set(names)):
            raise ValueError("proxies.static.items names must be unique")
        return self


class GatewaySource(SettingsModel):
    """One residential endpoint expanded into sticky sessions with stable ids."""

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    port_range: tuple[int, int] | None = None
    scheme: ProxyScheme = "http"
    sessions: int = Field(ge=1)
    user: str = Field(min_length=1)
    password: str | None = Field(None, repr=False)
    session_template: str = "{user}_s_{session}"
    password_template: str | None = None
    session_id_template: str = "{salt}{index:02d}g{generation}"
    name_template: str = "res_{index:02d}"
    session_salt: str = Field("ds", pattern=r"^[A-Za-z0-9]*$")
    country_code: str | None = None

    @model_validator(mode="after")
    def _session_is_pinned(self) -> GatewaySource:
        if (
            "{session}" not in self.session_template
            and "{session}" not in (self.password_template or "")
            and self.port_range is None
        ):
            raise ValueError(
                "proxies.gateway needs {session} in session_template or password_template, "
                "or a port_range: otherwise every session leaves through one exit IP"
            )
        if self.port_range is not None and self.port_range[1] < self.port_range[0]:
            raise ValueError("proxies.gateway.port_range must be [first, last]")
        templates = (self.session_id_template, self.session_template, self.password_template or "")
        if not any("{generation}" in template for template in templates):
            raise ValueError(
                "proxies.gateway needs {generation} in session_id_template (or the login or "
                "password template): without it a burned session cannot get a new exit IP"
            )
        return self


class ApiSource(SettingsModel):
    url: str | None = Field(None, repr=False)
    url_env: str | None = None
    method: Literal["GET", "POST"] = "GET"
    timeout_seconds: float = Field(15.0, gt=0)
    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    token: str | None = Field(None, repr=False)
    token_env: str | None = None
    token_header: str = "Authorization"
    token_header_template: str = "Bearer {token}"
    items_field: str | None = None
    field_map: dict[str, str] = Field(default_factory=dict)
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    scheme: ProxyScheme = "http"
    default_auth: ProxyAuth = ProxyAuth()
    fallback_to_static: bool = False

    @model_validator(mode="after")
    def _has_url(self) -> ApiSource:
        if not self.url and not self.url_env:
            raise ValueError("proxies.api requires url or url_env")
        return self


class ProxiesSettings(SettingsModel):
    schema_version: Literal[2] = 2
    source: Literal["none", "static", "gateway", "api"] = "none"
    static: StaticSource = StaticSource()
    gateway: GatewaySource | None = None
    api: ApiSource | None = None

    @model_validator(mode="after")
    def _source_is_configured(self) -> ProxiesSettings:
        if self.source == "gateway" and self.gateway is None:
            raise ValueError("proxies.source is gateway but proxies.gateway is missing")
        if self.source == "api" and self.api is None:
            raise ValueError("proxies.source is api but proxies.api is missing")
        return self


class ProxyHealthSettings(SettingsModel):
    consecutive_failures_to_open: int = Field(2, ge=1)
    cooldown_seconds: float = Field(300.0, ge=0)
    max_cooldown_seconds: float = Field(1800.0, ge=0)
    backoff_factor: float = Field(2.0, ge=1)
    immediate_open_categories: tuple[str, ...] = ("proxy_auth", "blocked", "captcha")
    tracked_categories: tuple[str, ...] = (
        "timeout",
        "network",
        "load_failed",
        "blank",
        "rate_limited",
        "browser_closed",
    )


class ProxyCheckSettings(SettingsModel):
    enabled: bool = True
    url: str = "https://api.ipify.org?format=json"
    ip_field: str | None = "ip"
    timeout_seconds: float = Field(15.0, gt=0)
    parallel: int = Field(8, ge=1)


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
    proxies: ProxiesSettings = ProxiesSettings()
    proxy_health: ProxyHealthSettings = ProxyHealthSettings()
    proxy_check: ProxyCheckSettings = ProxyCheckSettings()
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
        if self.browser.isolation == "persistent":
            # A profile directory can be open in one browser only; no setting overrides that.
            rules.append(LimitRule("profile", (Dimension.SITE, Dimension.PROXY), 1))
        return rules

    def selected_proxies(self, scraper: str) -> tuple[str, ...]:
        selection = next((item for item in self.scrapers if item.name == scraper), None)
        return selection.proxy_names if selection is not None else ()

    @property
    def database_path(self) -> Path:
        return self.storage.database or self.paths.data / "dailyscraper.sqlite"

    @property
    def profiles_dir(self) -> Path:
        return self.paths.data / "profiles"

    @property
    def extensions_dir(self) -> Path:
        return self.paths.data / "runtime" / "extensions"

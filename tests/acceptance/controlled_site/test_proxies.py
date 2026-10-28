"""Real Chromium through a local authenticating proxy, in both isolation modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import HealthStatus, RunStatus
from dailyscraper.output import iter_csv
from dailyscraper.settings import load_settings
from dailyscraper.settings.models import ProxiesSettings, ProxyCheckSettings
from dailyscraper.storage import open_store
from dailyscraper.transport.profiles import META_FILE
from tests.harness.connect_proxy import ProgrammableConnectProxy

pytestmark = pytest.mark.browser

SITE = "dailyscraper.com"
CREDENTIALS = ("scraper", "s3cret")


def proxied(project_root: Path, workspace: Path, items: list[dict], **browser):
    loaded = load_settings(project_root)
    return loaded.model_copy(
        update={
            "paths": loaded.paths.model_copy(update={"data": workspace / "data"}),
            "proxies": ProxiesSettings.model_validate(
                {
                    "source": "static",
                    "static": {
                        "default_auth": {"user": CREDENTIALS[0], "password": CREDENTIALS[1]},
                        "items": items,
                    },
                }
            ),
            "proxy_check": ProxyCheckSettings(url=f"https://{SITE}/tests/", ip_field=None),
            "browser": loaded.browser.model_copy(update=browser),
        }
    )


def item(name: str, proxy: ProgrammableConnectProxy) -> dict:
    return {"name": name, "host": proxy.endpoint.host, "port": proxy.endpoint.port}


@pytest.mark.parametrize("isolation", ["ephemeral", "persistent"])
def test_catalog_runs_through_an_authenticating_proxy(
    project_root: Path, scenario_workspace: Path, isolation: str
) -> None:
    with ProgrammableConnectProxy("tunnel", credentials=CREDENTIALS) as proxy:
        settings = proxied(
            project_root, scenario_workspace, [item("local", proxy)], isolation=isolation
        )
        store = open_store("sqlite", settings.database_path)
        report = RunService(settings, store).run(RunRequest(("controlled_catalog",)))

    assert report.run.status is RunStatus.COMPLETED, report.run.error
    assert len(list(iter_csv(report.outputs["csv"][0]))) == 19
    assert f"{SITE}:443" in proxy.log.tunnels
    assert report.run.summary["scrapers"]["controlled_catalog"]["proxies"] == {"local": 1}
    if isolation == "persistent":
        profile = settings.profiles_dir / SITE / "local"
        assert (profile / META_FILE).is_file()
        assert (settings.extensions_dir / SITE / "local" / "manifest.json").is_file()


def test_network_check_moves_the_run_off_a_rejecting_proxy(
    project_root: Path, scenario_workspace: Path
) -> None:
    with (
        ProgrammableConnectProxy("proxy_auth") as bad,
        ProgrammableConnectProxy("tunnel", credentials=CREDENTIALS) as good,
    ):
        settings = proxied(project_root, scenario_workspace, [item("bad", bad), item("good", good)])
        store = open_store("sqlite", settings.database_path)
        report = RunService(settings, store).run(RunRequest(("controlled_catalog",)))

    assert report.run.status is RunStatus.COMPLETED, report.run.error
    assert report.run.summary["scrapers"]["controlled_catalog"]["proxies"] == {"good": 1}
    assert store.proxy_health.states(SITE, ["bad"])["bad"].status is HealthStatus.OPEN

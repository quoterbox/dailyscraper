"""A real Chromium waits on a CAPTCHA page until a person solves it, notified via Telegram.

The page is served locally: it shows a challenge until the test, playing the
person, calls ``/solve``; then the page swaps itself to the catalog without a
reload, like a solved in-page challenge does.
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from dailyscraper.app.run_service import RunRequest, RunService
from dailyscraper.domain import RunStatus
from dailyscraper.output import iter_csv
from dailyscraper.settings import load_settings
from dailyscraper.settings.models import (
    InterventionSettings,
    ManualInterventionSettings,
    NotificationsSettings,
    TelegramChannelSettings,
)
from dailyscraper.storage import open_store

pytestmark = pytest.mark.browser

CHALLENGE = """<!doctype html><html><body>
<div id="app"><h1>Please solve the captcha to continue</h1></div>
<script>
const products = `<ul class="products">
  <li class="product"><h2 class="oa-single-product__title"><a href="/p/1">Ring 1</a></h2>
    <span class="oa-single-product__price">100</span></li>
  <li class="product"><h2 class="oa-single-product__title"><a href="/p/2">Ring 2</a></h2>
    <span class="oa-single-product__price">200</span></li>
</ul>`;
const timer = setInterval(async () => {
  const state = await (await fetch("/state")).json();
  if (state.solved) { clearInterval(timer); document.getElementById("app").innerHTML = products; }
}, 300);
</script></body></html>"""


@pytest.fixture
def challenge_site():
    solved = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/state":
                self._send("application/json", json.dumps({"solved": solved.is_set()}))
            elif self.path == "/solve":
                solved.set()
                self._send("application/json", "{}")
            else:
                self._send("text/html; charset=utf-8", CHALLENGE)

        def _send(self, content_type: str, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def challenge_package(project_root: Path, scrapers: Path, base: str) -> str:
    source = project_root / "tests" / "scrapers" / "controlled_catalog" / "scraper.json"
    definition = json.loads(source.read_text("utf-8"))
    definition["name"] = "challenge_catalog"
    definition["website"] = base
    definition["links"] = [{"id": "main", "type": "list", "urls": [f"{base}/catalog/"]}]
    definition["pages"]["list"]["pagination"]["last_page"] = 1
    target = scrapers / "challenge_catalog"
    target.mkdir(parents=True)
    (target / "scraper.json").write_text(json.dumps(definition), encoding="utf-8")
    return "challenge_catalog"


def test_captcha_waits_for_a_person_who_is_told_via_telegram(
    project_root: Path, scenario_workspace: Path, challenge_site: str, telegram_server, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    loaded = load_settings(project_root)
    settings = loaded.model_copy(
        update={
            "paths": loaded.paths.model_copy(
                update={
                    "data": scenario_workspace / "data",
                    "scrapers": scenario_workspace / "scrapers",
                }
            ),
            "interventions": InterventionSettings(
                manual=ManualInterventionSettings(
                    timeout_seconds=60, poll_interval_seconds=0.5, allow_headless=True
                )
            ),
            "notifications": NotificationsSettings(
                enabled=True,
                dispatch_interval_seconds=0.2,
                channels=(TelegramChannelSettings(type="telegram", api_root=telegram_server.url),),
            ),
        }
    )
    name = challenge_package(project_root, settings.paths.scrapers, challenge_site)
    store = open_store("sqlite", settings.database_path)
    reports = []
    runner = threading.Thread(
        target=lambda: reports.append(RunService(settings, store).run(RunRequest((name,))))
    )
    runner.start()

    alert = telegram_server.wait_for("CAPTCHA: manual action required", timeout=60)
    assert f"url: {challenge_site}/catalog/" in alert["text"]
    urllib.request.urlopen(f"{challenge_site}/solve", timeout=5).close()
    runner.join(timeout=120)

    (report,) = reports
    assert report.run.status is RunStatus.COMPLETED, report.run.error
    rows = list(iter_csv(report.outputs["csv"][0]))
    assert [row["item_name"] for row in rows] == ["Ring 1", "Ring 2"]
    types = [event.type for event in store.events.list(report.run.run_id)]
    assert "intervention.required" in types and "intervention.resolved" in types
    telegram_server.wait_for("Run finished: completed")

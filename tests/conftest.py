from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path

import pytest

from dailyscraper.settings.environment import (
    TEST_SESSION_ID_VARIABLE,
    TEST_WORKSPACE_VARIABLE,
)
from tests.harness.artifact_workspace import (
    RetainedWorkspace,
    ScenarioWorkspace,
    finalize_scenario_workspaces,
    register_scenario_workspace,
)

_SCENARIO_WORKSPACES: dict[str, ScenarioWorkspace] = {}
_FAILED_NODEIDS: set[str] = set()
_RETAINED_WORKSPACES: list[RetainedWorkspace] = []


def pytest_addoption(parser) -> None:
    group = parser.getgroup("dailyscraper acceptance")
    group.addoption(
        "--keep-test-artifacts",
        action="store_true",
        default=False,
        help="Keep every per-test workspace under .test-artifacts.",
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def fixtures_root(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures"


@pytest.fixture
def scenario_workspace(request) -> Path:
    """An isolated per-test directory, removed after success and kept after failure."""

    entry = register_scenario_workspace(
        Path(os.environ[TEST_WORKSPACE_VARIABLE]).resolve(),
        session_id=os.environ[TEST_SESSION_ID_VARIABLE],
        nodeid=request.node.nodeid,
    )
    _SCENARIO_WORKSPACES[request.node.nodeid] = entry
    return entry.root


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed:
        _FAILED_NODEIDS.add(item.nodeid)
    return report


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus: int) -> None:
    global _RETAINED_WORKSPACES

    _RETAINED_WORKSPACES = finalize_scenario_workspaces(
        _SCENARIO_WORKSPACES.values(),
        session_workspace=Path(os.environ[TEST_WORKSPACE_VARIABLE]).resolve(),
        failed_nodeids=_FAILED_NODEIDS,
        keep_all=session.config.getoption("keep_test_artifacts", default=False),
    )


def pytest_terminal_summary(terminalreporter) -> None:
    if not _RETAINED_WORKSPACES:
        return
    terminalreporter.section("retained test artifacts")
    for entry in _RETAINED_WORKSPACES:
        terminalreporter.write_line(f"{entry.nodeid}: {entry.root} ({entry.reason})")


@pytest.fixture
def ip_server():
    """A local HTTP endpoint answering like an IP echo service."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ip": "203.0.113.7"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/ip"
    server.shutdown()

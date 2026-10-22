from __future__ import annotations

import os
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

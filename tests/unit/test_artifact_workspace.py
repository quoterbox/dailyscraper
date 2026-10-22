from __future__ import annotations

import json
from pathlib import PureWindowsPath

import pytest

from tests.harness.artifact_workspace import (
    ScenarioWorkspace,
    finalize_scenario_workspaces,
    remove_scenario_workspace,
    scenario_workspace_name,
    scenario_workspace_path,
)

WINDOWS_LEGACY_PATH_BUDGET = 240
GITHUB_WINDOWS_CHECKOUT = PureWindowsPath("D:/a/dailyscraper/dailyscraper")


def test_scenario_workspace_is_stable_and_inside_session(tmp_path) -> None:
    first = scenario_workspace_path(
        tmp_path,
        session_id="session-1",
        nodeid="tests/test_demo.py::test_run[one-page]",
    )
    second = scenario_workspace_path(
        tmp_path,
        session_id="session-1",
        nodeid="tests/test_demo.py::test_run[all-pages]",
    )

    assert first.is_relative_to(tmp_path)
    assert second.is_relative_to(tmp_path)
    assert first != second
    assert first.name.startswith("demo-one-page-")
    assert len(first.name) <= 21


def test_windows_artifact_path_budget() -> None:
    session_id = "260731-151716-7996"
    nodeid = "tests/acceptance/external_sites/test_wb_smoke.py::test_wb_catalog_canary"
    scraper = "wb_sokolov_without_details"
    worst_case = (
        GITHUB_WINDOWS_CHECKOUT
        / ".test-artifacts"
        / session_id
        / scenario_workspace_name(nodeid)
        / "final_files"
        / "2026_07_31"
        / "2026_07_31__15_17_19"
        / scraper
        / f"{scraper}__2026_07_31__15_17_19.csv"
    )

    assert len(str(worst_case)) < WINDOWS_LEGACY_PATH_BUDGET, (
        f"Windows artifact path uses {len(str(worst_case))} characters: {worst_case}"
    )


@pytest.mark.parametrize(
    ("keep_all", "failed", "expected_reason"),
    [
        (False, set(), None),
        (True, set(), "--keep-test-artifacts"),
        (False, {"scenario"}, "test failure"),
    ],
)
def test_finalize_applies_retention_policy(
    tmp_path,
    keep_all,
    failed,
    expected_reason,
) -> None:
    root = tmp_path / "scenarios" / "scenario"
    root.mkdir(parents=True)
    (root / "result.csv").write_text("value\n1\n", encoding="utf-8")
    entry = ScenarioWorkspace(nodeid="scenario", root=root)

    retained = finalize_scenario_workspaces(
        [entry],
        session_workspace=tmp_path,
        failed_nodeids=failed,
        keep_all=keep_all,
    )

    if expected_reason is None:
        assert retained == []
        assert not root.exists()
    else:
        assert retained[0].reason == expected_reason
        assert (root / "result.csv").is_file()
        manifest = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "retained"
        assert manifest["reason"] == expected_reason


def test_cleanup_refuses_path_outside_session(tmp_path) -> None:
    outside = tmp_path.parent / "outside-artifacts"
    with pytest.raises(RuntimeError, match="outside session"):
        remove_scenario_workspace(outside, session_workspace=tmp_path)

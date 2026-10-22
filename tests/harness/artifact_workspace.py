from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ScenarioWorkspace:
    nodeid: str
    root: Path


@dataclass(frozen=True)
class RetainedWorkspace:
    nodeid: str
    root: Path
    reason: str


def scenario_workspace_path(
    session_workspace: Path,
    *,
    session_id: str,
    nodeid: str,
) -> Path:
    base = Path(session_workspace).resolve()
    session_root = base if base.name == session_id else base / session_id
    return (session_root / scenario_workspace_name(nodeid)).resolve()


def scenario_workspace_name(nodeid: str) -> str:
    """Return the bounded, deterministic directory name for one test."""

    file_part, _, test_part = nodeid.partition("::")
    file_label = Path(file_part).stem.removeprefix("test_")
    parameter = ""
    if "[" in test_part and test_part.endswith("]"):
        parameter = test_part.rsplit("[", 1)[1][:-1]
    readable_name = "-".join(part for part in (file_label, parameter) if part)
    normalized = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        readable_name,
    ).strip("-.")
    label = (normalized or "scenario")[:14].rstrip("-.")
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:6]
    return f"{label}-{digest}"


def register_scenario_workspace(
    session_workspace: Path,
    *,
    session_id: str,
    nodeid: str,
) -> ScenarioWorkspace:
    root = scenario_workspace_path(
        session_workspace,
        session_id=session_id,
        nodeid=nodeid,
    )
    entry = ScenarioWorkspace(nodeid=nodeid, root=root)
    write_scenario_manifest(entry, status="running")
    return entry


def finalize_scenario_workspaces(
    entries: Iterable[ScenarioWorkspace],
    *,
    session_workspace: Path,
    failed_nodeids: set[str],
    keep_all: bool,
) -> list[RetainedWorkspace]:
    base = Path(session_workspace).resolve()
    retained: list[RetainedWorkspace] = []
    for entry in entries:
        if keep_all or entry.nodeid in failed_nodeids:
            reason = "--keep-test-artifacts" if keep_all else "test failure"
            write_scenario_manifest(
                entry,
                status="retained",
                reason=reason,
            )
            retained.append(
                RetainedWorkspace(
                    nodeid=entry.nodeid,
                    root=entry.root,
                    reason=reason,
                )
            )
            continue
        remove_scenario_workspace(entry.root, session_workspace=base)
    return retained


def remove_scenario_workspace(
    workspace: Path,
    *,
    session_workspace: Path,
) -> None:
    root = Path(workspace).resolve()
    base = Path(session_workspace).resolve()
    if root == base or not root.is_relative_to(base):
        raise RuntimeError(f"Refusing to remove artifact path outside session: {root}")
    if root.exists():
        shutil.rmtree(root)
    _remove_empty_parents(root.parent, stop=base)


def write_scenario_manifest(
    entry: ScenarioWorkspace,
    *,
    status: str,
    reason: str | None = None,
) -> None:
    entry.root.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodeid": entry.nodeid,
        "status": status,
        "reason": reason,
        "workspace": str(entry.root),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (entry.root / "scenario.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = Path(path).resolve()
    boundary = Path(stop).resolve()
    while current != boundary and current.is_relative_to(boundary):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent

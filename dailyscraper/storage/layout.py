from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RUNS_DIRECTORY = "runs"


@dataclass(frozen=True)
class RunLayout:
    """Where a run keeps its files; partition paths in the registry are relative to ``root``."""

    root: Path

    @classmethod
    def for_run(cls, data_dir: Path, run_id: str) -> RunLayout:
        return cls(Path(data_dir) / RUNS_DIRECTORY / run_id)

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def summary(self) -> Path:
        return self.root / "summary.json"

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def partitions(self) -> Path:
        return self.root / "partitions"

    @property
    def output(self) -> Path:
        return self.root / "output"

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"path {relative!r} escapes the run directory")
        return path

    def relative(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

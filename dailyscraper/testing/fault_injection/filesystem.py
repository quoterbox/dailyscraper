from __future__ import annotations

from pathlib import Path

from .models import FaultPoint
from .runtime import get_fault_injector


def hit_filesystem_fault(point: FaultPoint, path: Path, *, scope: str) -> None:
    target = Path(path)
    get_fault_injector().hit(
        point,
        scope=scope,
        path_name=target.name,
        path_suffix=target.suffix.lower(),
        path_prefix=_path_prefix(target),
    )


def _path_prefix(path: Path) -> str:
    name = path.name
    if "__" in name:
        return name.split("__", 1)[0]
    return path.stem

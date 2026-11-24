# Copyright (c) 2026 JQ.
#
# DailyScraper - https://dailyscraper.com
# Source: https://github.com/quoterbox/dailyscraper
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterable
from pathlib import Path


def make_archive(files: Iterable[Path], target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=Path(path).name)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target

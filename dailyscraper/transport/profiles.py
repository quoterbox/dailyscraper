"""Persistent browser profiles, one per site and proxy.

A profile carries cookies and history tied to an exit identity. When the proxy
behind it changes (new address, credentials or gateway session), a site would
see old cookies arrive from a new IP, so the profile is started clean.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

META_FILE = "dailyscraper-profile.json"


def prepare_profile(directory: Path, identity: str, *, reset_on_change: bool) -> bool:
    """Create the profile directory; ``True`` when an existing profile was cleared."""
    meta = directory / META_FILE
    previous = None
    if meta.is_file():
        try:
            previous = json.loads(meta.read_text(encoding="utf-8")).get("identity")
        except (OSError, ValueError):
            previous = None
    cleared = False
    if reset_on_change and directory.exists() and previous is not None and previous != identity:
        logger.info("Profile %s belongs to another proxy identity, starting clean", directory)
        shutil.rmtree(directory, ignore_errors=True)
        cleared = True
    directory.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"identity": identity}), encoding="utf-8")
    return cleared

"""Persistent browser profiles, one per site and proxy.

A profile carries cookies and history tied to an exit identity. When the proxy
behind it changes (new address, credentials or gateway session), a site would
see old cookies arrive from a new IP, so the profile is started clean.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
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


STATE_FILE = "dailyscraper-state.json"


class ProfileState:
    """Session preparation done in a persistent profile, so the next session can skip it."""

    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = directory / STATE_FILE
        self._clock = clock

    def is_fresh(self, key: str, version: str, ttl_seconds: float) -> bool:
        if ttl_seconds <= 0:
            return False
        record = self._read().get(key)
        if not isinstance(record, dict) or record.get("version") != version:
            return False
        age = self._clock() - float(record.get("done_at", 0))
        return 0 <= age <= ttl_seconds

    def mark(self, key: str, version: str) -> None:
        state = self._read()
        state[key] = {"version": version, "done_at": self._clock()}
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(state), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            logger.warning("Cannot record profile state %s: %s", key, error)

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

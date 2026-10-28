from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from typing import Any

from dailyscraper.domain import CommitRecord
from dailyscraper.storage.contracts import PartitionRegistry, StorageError
from dailyscraper.storage.layout import RunLayout


class PartitionIntegrityError(StorageError):
    pass


def iter_committed_records(
    registry: PartitionRegistry,
    layout: RunLayout,
    run_id: str,
    scraper: str,
    *,
    commits: Iterable[CommitRecord] | None = None,
) -> Iterator[dict[str, Any]]:
    """Records of registered commits in registration order; unregistered bytes are ignored."""
    for commit in registry.commits(run_id, scraper) if commits is None else commits:
        path = layout.resolve(commit.partition.path)
        size = commit.end_offset - commit.start_offset
        try:
            with path.open("rb") as stream:
                stream.seek(commit.start_offset)
                payload = stream.read(size)
        except FileNotFoundError as error:
            raise PartitionIntegrityError(f"partition file is missing: {path}") from error
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != commit.checksum:
            raise PartitionIntegrityError(
                f"commit {commit.commit_id} does not match {path}"
                f" at {commit.start_offset}..{commit.end_offset}"
            )
        for line in payload.splitlines():
            yield json.loads(line)

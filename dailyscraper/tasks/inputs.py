from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dailyscraper.schema import Inputs

SNAPSHOT_ENCODING = "utf-8"
SNAPSHOT_DELIMITER = ";"
MANIFEST_VERSION = 1


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class InputSnapshot:
    """An immutable copy of a run's input; detail rows refer to it by 1-based position."""

    path: Path
    manifest: Path
    rows: int
    fieldnames: tuple[str, ...]
    sha256: str
    skipped_invalid: int = 0
    skipped_duplicates: int = 0

    def iter_rows(self) -> Iterator[dict[str, str]]:
        with self.path.open(encoding=SNAPSHOT_ENCODING, newline="") as stream:
            for row in csv.DictReader(stream, delimiter=SNAPSHOT_DELIMITER):
                yield dict(row)


def create_input_snapshot(source: Path, target: Path, inputs: Inputs) -> InputSnapshot:
    """Validate, deduplicate and copy ``source`` to ``target`` in the snapshot format."""
    source = Path(source)
    with source.open(encoding=inputs.encoding, newline="") as stream:
        reader = csv.DictReader(stream, delimiter=inputs.delimiter)
        fieldnames = tuple(reader.fieldnames or ())
        missing = [name for name in inputs.required_fields if name not in fieldnames]
        if missing:
            raise InputError(f"{source} has no required columns {missing}")
        rows, invalid, duplicates = [], 0, 0
        seen: set[tuple[str, ...]] = set()
        for row in reader:
            if any(not (row.get(name) or "").strip() for name in inputs.required_fields):
                invalid += 1
                continue
            if inputs.deduplicate_by_fields:
                identity = tuple(
                    (row.get(name) or "").strip() for name in inputs.deduplicate_by_fields
                )
                if identity in seen:
                    duplicates += 1
                    continue
                seen.add(identity)
            rows.append({name: row.get(name) or "" for name in fieldnames})

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding=SNAPSHOT_ENCODING, newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=SNAPSHOT_DELIMITER)
        writer.writeheader()
        writer.writerows(rows)
    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = target.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "version": MANIFEST_VERSION,
                "source": source.name,
                "rows": len(rows),
                "fieldnames": list(fieldnames),
                "sha256": sha256,
                "skipped_invalid": invalid,
                "skipped_duplicates": duplicates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return InputSnapshot(
        path=target,
        manifest=manifest,
        rows=len(rows),
        fieldnames=fieldnames,
        sha256=sha256,
        skipped_invalid=invalid,
        skipped_duplicates=duplicates,
    )


def load_input_snapshot(path: Path) -> InputSnapshot:
    path = Path(path)
    manifest = path.with_suffix(".manifest.json")
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    if raw.get("version") != MANIFEST_VERSION:
        raise InputError(f"unsupported input manifest {manifest}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != raw["sha256"]:
        raise InputError(f"input snapshot {path} does not match its manifest")
    return InputSnapshot(
        path=path,
        manifest=manifest,
        rows=raw["rows"],
        fieldnames=tuple(raw["fieldnames"]),
        sha256=raw["sha256"],
        skipped_invalid=raw.get("skipped_invalid", 0),
        skipped_duplicates=raw.get("skipped_duplicates", 0),
    )

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from openpyxl import Workbook

from dailyscraper.testing.fault_injection import FaultPoint, get_fault_injector

Row = dict[str, Any]
RowSource = Callable[[], Iterable[Row]]

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"


def columns_of(rows: Iterable[Row]) -> list[str]:
    return list(dict.fromkeys(key for row in rows for key in row))


def write_output(fmt: str, rows: RowSource, path: Path) -> int:
    """Write rows atomically; ``rows`` is called twice when columns must be known first."""
    writers = {"csv": _write_csv, "json": _write_json, "xlsx": _write_xlsx, "xml": _write_xml}
    if fmt not in writers:
        raise ValueError(f"unknown output format {fmt!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        count = writers[fmt](rows, temporary)
        get_fault_injector().hit(FaultPoint.FILESYSTEM_OUTPUT_BEFORE_REPLACE, path=path.name)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _write_csv(rows: RowSource, path: Path) -> int:
    columns = columns_of(rows())
    count = 0
    with path.open("w", encoding=CSV_ENCODING, newline="") as stream:
        writer = csv.writer(stream, delimiter=CSV_DELIMITER)
        writer.writerow(columns)
        for row in rows():
            writer.writerow([_text(row.get(column)) for column in columns])
            count += 1
    return count


def _write_json(rows: RowSource, path: Path) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("[")
        for row in rows():
            stream.write(",\n" if count else "\n")
            stream.write(json.dumps(row, ensure_ascii=False))
            count += 1
        stream.write("\n]\n" if count else "]\n")
    return count


def _write_xlsx(rows: RowSource, path: Path) -> int:
    columns = columns_of(rows())
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("data")
    sheet.append(columns)
    count = 0
    for row in rows():
        sheet.append([_text(row.get(column)) for column in columns])
        count += 1
    workbook.save(path)
    return count


def _write_xml(rows: RowSource, path: Path) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write('<?xml version="1.0" encoding="utf-8"?>\n<rows>\n')
        for row in rows():
            stream.write("  <row>\n")
            for name, value in row.items():
                stream.write(
                    f"    <field name={quoteattr(str(name))}>{escape(_text(value))}</field>\n"
                )
            stream.write("  </row>\n")
            count += 1
        stream.write("</rows>\n")
    return count


def iter_csv(path: Path) -> Iterator[Row]:
    with Path(path).open(encoding=CSV_ENCODING, newline="") as stream:
        yield from csv.DictReader(stream, delimiter=CSV_DELIMITER)

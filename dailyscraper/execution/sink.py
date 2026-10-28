from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO


class ItemSink(Protocol):
    def emit(
        self, item: dict[str, Any], *, input_row: int | None = None, status: str | None = None
    ) -> None:
        """``input_row`` is the 1-based input position of a detail row, ``status`` its outcome."""
        ...


@dataclass(frozen=True)
class EmittedRow:
    item: dict[str, Any]
    input_row: int | None
    status: str | None


class MemorySink:
    def __init__(self) -> None:
        self.rows: list[EmittedRow] = []

    @property
    def items(self) -> list[dict[str, Any]]:
        return [row.item for row in self.rows]

    def emit(
        self, item: dict[str, Any], *, input_row: int | None = None, status: str | None = None
    ) -> None:
        self.rows.append(EmittedRow(dict(item), input_row, status))


class JsonLinesSink:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.count = 0
        self._stream: TextIO | None = None

    def __enter__(self) -> JsonLinesSink:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("w", encoding="utf-8", newline="\n")
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def emit(
        self, item: dict[str, Any], *, input_row: int | None = None, status: str | None = None
    ) -> None:
        if self._stream is None:
            raise RuntimeError("JsonLinesSink must be opened with 'with' before emitting")
        self._stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._stream.flush()
        self.count += 1

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, TextIO


class ItemSink(Protocol):
    def emit(self, item: dict[str, Any]) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def emit(self, item: dict[str, Any]) -> None:
        self.items.append(dict(item))


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

    def emit(self, item: dict[str, Any]) -> None:
        if self._stream is None:
            raise RuntimeError("JsonLinesSink must be opened with 'with' before emitting")
        self._stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._stream.flush()
        self.count += 1

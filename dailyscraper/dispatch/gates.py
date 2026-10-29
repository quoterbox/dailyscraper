"""Per-scraper gates: a scraper's work waits for its health probe, nobody else's."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from dailyscraper.domain import TaskKind

PROBE_ONLY = frozenset({TaskKind.PROBE})


class GateState(StrEnum):
    OPEN = "open"
    PROBING = "probing"
    CLOSED = "closed"


@dataclass
class Gate:
    state: GateState
    reason: str = ""


class Gates:
    def __init__(self, scrapers: Iterable[str], *, probing: Collection[str] = ()) -> None:
        self._gates = {
            name: Gate(GateState.PROBING if name in probing else GateState.OPEN, "")
            for name in scrapers
        }

    def state(self, scraper: str) -> GateState:
        return self._gates[scraper].state

    def claimable(
        self, scrapers: Sequence[str]
    ) -> list[tuple[list[str], frozenset[TaskKind] | None]]:
        """Groups of scrapers with the task kinds each may take now; ``None``: any kind."""
        probing = [name for name in scrapers if self.state(name) is GateState.PROBING]
        open_ = [name for name in scrapers if self.state(name) is GateState.OPEN]
        groups: list[tuple[list[str], frozenset[TaskKind] | None]] = []
        if probing:
            groups.append((probing, PROBE_ONLY))
        if open_:
            groups.append((open_, None))
        return groups

    def open(self, scraper: str, reason: str) -> None:
        self._gates[scraper] = Gate(GateState.OPEN, reason)

    def close(self, scraper: str, reason: str) -> None:
        self._gates[scraper] = Gate(GateState.CLOSED, reason)

    def summary(self) -> dict[str, dict[str, str]]:
        return {
            name: {"state": gate.state.value, "reason": gate.reason}
            for name, gate in self._gates.items()
        }

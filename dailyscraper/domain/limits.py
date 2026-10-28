from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

DIRECT_PROXY = "direct"


class Dimension(StrEnum):
    """What a session is counted by. A rule's scope is any combination of them."""

    HOST = "host"
    SITE = "site"
    PROXY = "proxy"
    SCRAPER = "scraper"
    RUN = "run"
    PROJECT = "project"
    KIND = "kind"


@dataclass(frozen=True)
class LimitRule:
    """At most ``limit`` sessions per distinct value of ``scope``.

    ``match`` narrows the rule to sessions with these values. Rules with the
    same name are one limit with overrides: the matching rule with the most
    ``match`` conditions wins, and of equally specific ones the later, so a site
    can get a higher or a lower limit than the default and a configured rule
    replaces a built-in one. Rules with different names all apply.
    """

    name: str
    scope: tuple[Dimension, ...]
    limit: int
    match: Mapping[Dimension, str] = field(default_factory=lambda: MappingProxyType({}))

    def applies_to(self, session: Mapping[Dimension, str]) -> bool:
        return all(session.get(dimension) == value for dimension, value in self.match.items())

    def key(self, session: Mapping[Dimension, str]) -> str:
        values = "|".join(
            f"{dimension.value}={session.get(dimension, '')}" for dimension in self.scope
        )
        return f"{self.name}:{values}"


@dataclass(frozen=True)
class LeaseKey:
    key: str
    limit: int


def lease_keys(rules: Iterable[LimitRule], session: Mapping[Dimension, str]) -> list[LeaseKey]:
    """Counters a session takes a place in, one per rule name."""
    chosen: dict[str, LimitRule] = {}
    for rule in rules:
        if not rule.applies_to(session):
            continue
        current = chosen.get(rule.name)
        if current is None or len(rule.match) >= len(current.match):
            chosen[rule.name] = rule
    return [LeaseKey(rule.key(session), rule.limit) for rule in chosen.values()]

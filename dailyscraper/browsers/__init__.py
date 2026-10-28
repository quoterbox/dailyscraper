"""Slot processes: one browser session each, managed by the parent within host limits."""

from dailyscraper.browsers.manager import (
    SLOT_LEASE,
    BrowserManager,
    SlotEvent,
    SlotExited,
    SlotHandle,
    SlotMessage,
    SlotState,
)
from dailyscraper.browsers.slot import DEFAULT_OPENER, SlotConfig

__all__ = [
    "DEFAULT_OPENER",
    "SLOT_LEASE",
    "BrowserManager",
    "SlotConfig",
    "SlotEvent",
    "SlotExited",
    "SlotHandle",
    "SlotMessage",
    "SlotState",
]

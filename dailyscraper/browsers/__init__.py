"""Slot processes: one browser session each, managed by the parent within host limits."""

from dailyscraper.browsers.manager import (
    BROWSER_KIND,
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
    "BROWSER_KIND",
    "BrowserManager",
    "SlotConfig",
    "SlotEvent",
    "SlotExited",
    "SlotHandle",
    "SlotMessage",
    "SlotState",
]

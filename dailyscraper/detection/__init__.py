"""Page state detection and the single error classifier."""

from dailyscraper.detection.classifier import (
    PageProbe,
    classify_page,
    is_hard_terminal,
    ready_is_sufficient,
)
from dailyscraper.detection.errors import (
    category_for_state,
    classify_error,
    state_for_navigation_error,
)
from dailyscraper.detection.policy import Signals, StatePolicy, build_policy
from dailyscraper.detection.waiter import StateWaiter

__all__ = [
    "PageProbe",
    "Signals",
    "StatePolicy",
    "StateWaiter",
    "build_policy",
    "category_for_state",
    "classify_error",
    "classify_page",
    "is_hard_terminal",
    "ready_is_sufficient",
    "state_for_navigation_error",
]

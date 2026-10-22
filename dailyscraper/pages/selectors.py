from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from dailyscraper.pages.base import FrozenModel, Seconds


class SelectorKind(StrEnum):
    CSS = "css"
    XPATH = "xpath"


class Locator(FrozenModel):
    kind: SelectorKind
    value: str = Field(min_length=1)


class PrepareMode(StrEnum):
    FIND = "find"
    REMOVE = "remove"
    REPLACE = "replace"


class ValuePrepare(FrozenModel):
    regex: str = Field(min_length=1)
    mode: PrepareMode = PrepareMode.FIND
    replace_with: str = ""

    @field_validator("regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"invalid regex {value!r}: {error}") from error
        return value

    def apply(self, value: str) -> str | None:
        match self.mode:
            case PrepareMode.REMOVE:
                return re.sub(self.regex, "", value)
            case PrepareMode.REPLACE:
                return re.sub(self.regex, self.replace_with, value)
            case PrepareMode.FIND:
                found = re.search(self.regex, value)
                return found.group(0) if found else None


class Selector(FrozenModel):
    """Ordered locator alternatives for one element.

    JSON accepts ``{"css": ...}`` or ``{"xpath": ...}`` (a string or a list) as a
    shorthand, or ``{"selectors": [{"css": ...}, {"xpath": ...}]}`` when the order
    across kinds matters.
    """

    locators: tuple[Locator, ...] = Field(min_length=1)
    attribute: str | None = None
    wait: Seconds | None = None
    prepare: ValuePrepare | None = None
    parent_text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _expand_shorthand(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "locators" in data:
            return data
        shorthand = [kind for kind in SelectorKind if kind.value in data]
        if "selectors" in data and shorthand:
            raise ValueError("use either 'selectors' or a css/xpath shorthand, not both")
        if len(shorthand) > 1:
            raise ValueError(
                "both 'css' and 'xpath' are set: list the alternatives in 'selectors' "
                "to make their order explicit"
            )
        expanded = {
            key: value
            for key, value in data.items()
            if key != "selectors" and key not in SelectorKind
        }
        if shorthand:
            kind = shorthand[0]
            values = data[kind.value]
            values = [values] if isinstance(values, str) else values
            expanded["locators"] = [{"kind": kind, "value": value} for value in values]
        else:
            expanded["locators"] = [_expand_option(option) for option in data.get("selectors", [])]
        return expanded

    @property
    def xpaths(self) -> tuple[str, ...]:
        return tuple(loc.value for loc in self.locators if loc.kind is SelectorKind.XPATH)

    @property
    def css(self) -> tuple[str, ...]:
        return tuple(loc.value for loc in self.locators if loc.kind is SelectorKind.CSS)


def _expand_option(option: Any) -> Any:
    if not isinstance(option, dict) or len(option) != 1:
        raise ValueError("each entry of 'selectors' must be {'css': ...} or {'xpath': ...}")
    ((kind, value),) = option.items()
    return {"kind": kind, "value": value}

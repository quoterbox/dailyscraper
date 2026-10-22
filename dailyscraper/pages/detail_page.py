from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from dailyscraper.pages.base import DelayRange, FrozenModel, Seconds
from dailyscraper.pages.common import ItemSample, Scroll
from dailyscraper.pages.selectors import Selector, ValuePrepare
from dailyscraper.pages.state import PageStateRules

PROPERTY_NAME_FIELD = "name"
PROPERTY_VALUE_FIELD = "value"


class PropertySpec(FrozenModel):
    label: str = Field(min_length=1)
    attribute: str | None = None
    prepare: ValuePrepare | None = None


class PropertyGroup(FrozenModel):
    """A name/value table on a detail page; each row's name cell is matched by label."""

    body: Selector | None = None
    item: Selector | None = None
    first_item: ItemSample
    second_item: ItemSample | None = None
    properties: dict[str, PropertySpec] = Field(min_length=1)
    required: bool = False

    @field_validator("properties", mode="before")
    @classmethod
    def _label_shorthand(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            name: {"label": spec} if isinstance(spec, str) else spec for name, spec in value.items()
        }

    @field_validator("first_item")
    @classmethod
    def _has_name_and_value(cls, value: ItemSample) -> ItemSample:
        missing = {PROPERTY_NAME_FIELD, PROPERTY_VALUE_FIELD} - set(value.fields)
        if missing:
            raise ValueError(f"first_item.fields must define {sorted(missing)}")
        return value


class DetailTemplate(FrozenModel):
    name: str = Field(min_length=1)
    ready: Selector | None = None
    fields: dict[str, Selector] = Field(default_factory=dict)
    required_fields: tuple[str, ...] = ()
    property_groups: dict[str, PropertyGroup] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _required_fields_exist(self) -> DetailTemplate:
        unknown = sorted(set(self.required_fields) - set(self.fields))
        if unknown:
            raise ValueError(f"required_fields are not defined in fields: {unknown}")
        return self


class DetailWaits(FrozenModel):
    page: Seconds = 30
    field: Seconds = 1
    property: Seconds = 1


class DetailDelays(FrozenModel):
    between_items: DelayRange = (0, 0)


class DetailPage(FrozenModel):
    templates: tuple[DetailTemplate, ...] = Field(min_length=1)
    start_item: int = Field(1, ge=1)
    last_item: int = Field(0, ge=0)
    link_field: str = "detail_link"
    waits: DetailWaits = DetailWaits()
    delays: DetailDelays = DetailDelays()
    scroll: Scroll = Scroll()
    state: PageStateRules | None = None


def property_output_field(group: str, name: str) -> str:
    return f"property_{group}_{name}"

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _ordered[T: (int, float)](value: tuple[T, T]) -> tuple[T, T]:
    if value[0] > value[1]:
        raise ValueError(f"range minimum {value[0]} is greater than maximum {value[1]}")
    return value


Seconds = Annotated[float, Field(ge=0)]
DelayRange = Annotated[
    tuple[Annotated[float, Field(ge=0)], Annotated[float, Field(ge=0)]],
    AfterValidator(_ordered),
]
OffsetRange = Annotated[tuple[int, int], AfterValidator(_ordered)]

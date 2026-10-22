from __future__ import annotations

import re

_INDEX = re.compile(r"^(?P<base>.+)\[(?P<index>\d+)\]$")


class XPathError(ValueError):
    pass


def split_xpath(xpath: str) -> list[str]:
    """Split on ``/`` outside predicates and string literals; ``//`` yields an empty step."""

    steps: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in xpath:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "/" and depth == 0:
            steps.append("".join(current))
            current = []
            continue
        current.append(char)
    if quote or depth:
        raise XPathError(f"unbalanced quotes or brackets in XPath: {xpath!r}")
    steps.append("".join(current))
    return steps


def join_xpath(steps: list[str]) -> str:
    return "/".join(steps)


def strip_index(step: str) -> tuple[str, int] | None:
    match = _INDEX.match(step)
    if match is None:
        return None
    return match["base"], int(match["index"])


def derive_item_xpath(first: str, second: str) -> str:
    """XPath of the element collection that two sibling items belong to.

    The two absolute XPaths must differ in exactly one step, and only by its
    positional index: ``.../li[1]/a`` and ``.../li[2]/a`` give ``.../li``.
    """

    first_steps = split_xpath(first)
    second_steps = split_xpath(second)
    if len(first_steps) != len(second_steps):
        raise XPathError(f"items have different depth: {first!r} and {second!r}")

    differing = [
        position
        for position, (left, right) in enumerate(zip(first_steps, second_steps, strict=True))
        if left != right
    ]
    if not differing:
        raise XPathError(f"items have identical XPath: {first!r}")
    if len(differing) > 1:
        raise XPathError(f"items differ in more than one step: {first!r} and {second!r}")

    position = differing[0]
    left = strip_index(first_steps[position])
    right = strip_index(second_steps[position])
    if left is None or right is None or left[0] != right[0]:
        raise XPathError(
            f"items must differ only by a positional index, got "
            f"{first_steps[position]!r} and {second_steps[position]!r}"
        )
    return join_xpath([*first_steps[:position], left[0]])


def item_xpath(item_collection: str, index: int) -> str:
    if index < 1:
        raise XPathError("XPath positions start at 1")
    return f"{item_collection}[{index}]"


def relative_to_item(item_collection: str, absolute: str) -> str:
    collection_steps = split_xpath(item_collection)
    absolute_steps = split_xpath(absolute)
    depth = len(collection_steps)
    item_step = strip_index(absolute_steps[depth - 1]) if len(absolute_steps) >= depth else None
    if (
        absolute_steps[: depth - 1] != collection_steps[:-1]
        or item_step is None
        or item_step[0] != collection_steps[-1]
    ):
        raise XPathError(f"{absolute!r} is not inside an item of {item_collection!r}")
    rest = absolute_steps[depth:]
    return join_xpath([".", *rest]) if rest else "."


def is_relative(xpath: str) -> bool:
    return xpath.startswith(".")

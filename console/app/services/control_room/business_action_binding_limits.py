from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MAX_EXPLICIT_ACTION_BINDINGS_BYTES = 65_536
MAX_EXPLICIT_ACTION_BINDING_DEPTH = 4


def _json_string_size(value: str, *, remaining: int) -> int | None:
    if len(value) + 2 > remaining:
        return None
    total = 2
    for char in value:
        codepoint = ord(char)
        if char in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            total += 2
        elif codepoint < 0x20 or codepoint <= 0xFFFF and codepoint > 0x7F:
            total += 6
        elif codepoint > 0xFFFF:
            total += 12
        else:
            total += 1
        if total > remaining:
            return None
    return total


def binding_collection_within_limits(value: Any) -> bool:
    total = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_EXPLICIT_ACTION_BINDING_DEPTH:
            return False
        if isinstance(current, str):
            size = _json_string_size(
                current,
                remaining=MAX_EXPLICIT_ACTION_BINDINGS_BYTES - total,
            )
            if size is None:
                return False
            total += size
        elif isinstance(current, Mapping):
            total += 2 + len(current) + max(0, len(current) - 1)
            for key, nested in current.items():
                if not isinstance(key, str):
                    return False
                stack.append((nested, depth + 1))
                stack.append((key, depth + 1))
        elif isinstance(current, list):
            total += 2 + max(0, len(current) - 1)
            stack.extend((nested, depth + 1) for nested in current)
        else:
            return False
        if total > MAX_EXPLICIT_ACTION_BINDINGS_BYTES:
            return False
    return True


__all__ = (
    "MAX_EXPLICIT_ACTION_BINDING_DEPTH",
    "MAX_EXPLICIT_ACTION_BINDINGS_BYTES",
    "binding_collection_within_limits",
)

from __future__ import annotations

import re

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_identifier(value: str, kind: str = "identifier") -> str:
    if not isinstance(value, str) or not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"Invalid {kind}: {value!r}. Must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
        )
    return value


def validate_bounded_int(value, kind: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Invalid {kind}: {value!r} (expected int)")
    try:
        coerced = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {kind}: {value!r} (expected int)") from exc
    if coerced < lo or coerced > hi:
        raise ValueError(f"{kind} must be {lo}..{hi}, got {coerced}")
    return coerced

"""
Identifier validators for the MCP-infra tool surface.

Sprint v1.35 (audit B3 P0): every tool here ran user-supplied schema/
table/entity strings through f-strings into SQL (or into S3 paths that
then went through DuckDB f-strings), which made simple SQL injection
trivial — e.g. ``table = 'users"; DROP TABLE users; --'`` would close
the surrounding quote and execute. Combined with audit B2 (already
fixed) it was a privilege-escalation primitive; on its own it is still
a data-tampering primitive for any caller that can reach the tool.

The validator matches refinement.app.duckdb_engine.SAFE_IDENTIFIER_RE
(which has been live since sprint v1.6) so the platform speaks a single
language about what "an identifier the user can supply" looks like.
"""
from __future__ import annotations

import re

# ASCII letters, digits and underscore, must start with a letter or underscore.
# Same regex used by refinement/app/duckdb_engine.py:36 since sprint v1.6.
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_identifier(value: str, kind: str = "identifier") -> str:
    """Return ``value`` if it is a safe SQL identifier; raise otherwise.

    The check is strict ASCII so Unicode look-alikes (Cyrillic ``а``,
    right-to-left override, zero-width joiners) are rejected.
    """
    if not isinstance(value, str) or not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"Invalid {kind}: {value!r}. Must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
        )
    return value


def validate_bounded_int(value, kind: str, lo: int, hi: int) -> int:
    """Coerce ``value`` to ``int`` and enforce ``lo <= value <= hi``.

    Used for LIMIT / row-cap parameters that go into f-strings. A string
    like ``"10; DROP TABLE x; --"`` raises here before it reaches SQL.
    Booleans are rejected explicitly because ``bool`` is a subclass of
    ``int`` and ``True`` would otherwise round-trip as ``1``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Invalid {kind}: {value!r} (expected int)")
    try:
        coerced = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {kind}: {value!r} (expected int)") from exc
    if coerced < lo or coerced > hi:
        raise ValueError(f"{kind} must be {lo}..{hi}, got {coerced}")
    return coerced

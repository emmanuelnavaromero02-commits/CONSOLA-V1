from __future__ import annotations

import re
import uuid as _uuid
from typing import Any


def coerce_uuid_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(_uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def coerce_uuid_list(values: Any) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for v in values:
        coerced = coerce_uuid_or_none(v)
        if coerced is not None:
            out.append(coerced)
    return out


_table_cache: dict[str, bool] = {}


async def has_table_cached(pool: Any, table_name: str) -> bool:
    if _table_cache.get(table_name) is True:
        return True
    present = bool(
        await pool.fetchval(f"SELECT to_regclass('public.{table_name}')")
    )
    if present:
        _table_cache[table_name] = True
    return present


def reset_table_cache() -> None:
    _table_cache.clear()


_STOPWORDS_ES = frozenset({
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "o", "que",
    "en", "a", "por", "para", "con", "sin", "es", "se", "no", "si",
    "me", "te", "le", "nos", "su", "mi", "tu", "lo",
})

_WORD_RE = re.compile(r"[a-záéíóúñ0-9_]+", re.IGNORECASE | re.UNICODE)


def tokenize_intent(text: str) -> set[str]:
    if not text:
        return set()
    raw = _WORD_RE.findall(text.lower())
    return {t for t in raw if len(t) > 2 and t not in _STOPWORDS_ES}

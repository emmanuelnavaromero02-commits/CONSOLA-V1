"""Sprint v1.45 advanced copilot — shared helpers for the new copilot modules.

These small primitives live in a private module so lessons_service,
goal_solver, watchdog_registry and briefing_v2 can share the same
UUID coercion, table-existence cache, and tokenizer without
importing each other (which would create circular deps).
"""
from __future__ import annotations

import re
import uuid as _uuid
from typing import Any


# ── UUID coercion ─────────────────────────────────────────────────────


def coerce_uuid_or_none(value: Any) -> str | None:
    """Return ``value`` as a canonical UUID string, or None if it's not
    a parseable UUID. Use this before any ``$X::uuid`` parameter so a
    bad string from the client doesn't crash the SQL with
    ``invalid input for type uuid``.
    """
    if value is None or value == "":
        return None
    try:
        return str(_uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def coerce_uuid_list(values: Any) -> list[str]:
    """Drop every element of ``values`` that isn't a valid UUID. Use
    before passing into a ``$X::uuid[]`` parameter."""
    if not values:
        return []
    out: list[str] = []
    for v in values:
        coerced = coerce_uuid_or_none(v)
        if coerced is not None:
            out.append(coerced)
    return out


# ── Table-existence memo ──────────────────────────────────────────────
#
# A new copilot module typically calls ``_has_table()`` at the top of
# every public function so the service degrades gracefully on older
# deployments where migration 93 hasn't been applied yet. That check
# is a SELECT to_regclass(...) — cheap, but it runs on every copilot
# turn. Once a table is observed to exist it can't disappear (we don't
# DROP), so memoise per-process. The cache is reset when the asyncpg
# pool is rebuilt (which happens on every process restart).


_table_cache: dict[str, bool] = {}


async def has_table_cached(pool: Any, table_name: str) -> bool:
    """Return True when ``public.<table_name>`` is observable through
    ``pool.fetchval('SELECT to_regclass(...)')``. The result is cached
    process-wide once it's True; a False answer is never cached so a
    deploy that adds the migration mid-run picks up the new tables
    on the next request.

    No lock guards the cache: it's a plain dict and the only racing
    callers can do on a cold start is each fire one idempotent
    ``SELECT to_regclass`` before the first True lands. That's cheaper
    than carrying a module-level ``asyncio.Lock`` — which would bind to
    whatever event loop imported the module and then blow up under a
    second loop (uvicorn reload, pytest-asyncio, ``asyncio.run`` in a
    worker).
    """
    if _table_cache.get(table_name) is True:
        return True
    present = bool(
        await pool.fetchval(f"SELECT to_regclass('public.{table_name}')")
    )
    if present:
        _table_cache[table_name] = True
    return present


def reset_table_cache() -> None:
    """Test hook: forget what we know about table presence."""
    _table_cache.clear()


# ── Tokeniser shared by lessons + watchdog matchers ────────────────────


_STOPWORDS_ES = frozenset({
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "o", "que",
    "en", "a", "por", "para", "con", "sin", "es", "se", "no", "si",
    "me", "te", "le", "nos", "su", "mi", "tu", "lo",
})

_WORD_RE = re.compile(r"[a-záéíóúñ0-9_]+", re.IGNORECASE | re.UNICODE)


def tokenize_intent(text: str) -> set[str]:
    """Split ``text`` into a set of useful tokens for keyword matching.

    Drops Spanish stopwords and very short fragments so a 3-word query
    doesn't match every lesson on file via incidental "de"/"la" hits.
    Used by both ``lessons_service`` (lesson relevance scoring) and
    ``watchdog_registry`` (watchdog intent matching) so a query that
    matches a lesson matches a watchdog the same way.
    """
    if not text:
        return set()
    raw = _WORD_RE.findall(text.lower())
    return {t for t in raw if len(t) > 2 and t not in _STOPWORDS_ES}

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable

from app.services import auth
from app.services._copilot_helpers import has_table_cached, tokenize_intent


logger = logging.getLogger(__name__)


_MAX_LIST_LIMIT      = 200
_MAX_KEYWORDS        = 32
_KEYWORD_MAX_LEN     = 64


VALID_RISK = ("read", "write", "destructive")


async def _has_table() -> bool:
    pool = await auth.pool()
    return await has_table_cached(pool, "copilot_watchdogs")


async def register_watchdog(
    *,
    cartridge_id: str,
    slug: str,
    name: str,
    description: str = "",
    intent_keywords: Iterable[str] | None = None,
    agent_slug: str | None = None,
    tools: Iterable[str] | None = None,
    risk_level: str = "read",
    enabled: bool = True,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    if not await _has_table():
        return None
    if not cartridge_id or not slug or not name:
        return None
    if risk_level not in VALID_RISK:
        risk_level = "read"

    cartridge_id = cartridge_id.strip().lower()[:80]
    slug = slug.strip().lower()[:80]
    name = name.strip()[:200]
    description = (description or "").strip()[:1000]

    keywords = _clean_string_list(intent_keywords, _MAX_KEYWORDS, _KEYWORD_MAX_LEN)
    tools_clean = _clean_string_list(tools, _MAX_KEYWORDS, 200)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)

    pool = await auth.pool()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO copilot_watchdogs
                (cartridge_id, slug, name, description, intent_keywords,
                 agent_slug, tools, risk_level, enabled, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (cartridge_id, slug) DO UPDATE
               SET name            = EXCLUDED.name,
                   description     = EXCLUDED.description,
                   intent_keywords = EXCLUDED.intent_keywords,
                   agent_slug      = EXCLUDED.agent_slug,
                   tools           = EXCLUDED.tools,
                   risk_level      = EXCLUDED.risk_level,
                   enabled         = EXCLUDED.enabled,
                   metadata        = EXCLUDED.metadata
            RETURNING id::text
            """,
            cartridge_id, slug, name, description, keywords,
            agent_slug, tools_clean, risk_level, enabled, meta_json,
        )
    finally:
        invalidate_list_cache()
    return row["id"]


def _clean_string_list(
    src: Iterable[str] | None, max_count: int, max_len: int
) -> list[str]:
    if not src:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in src:
        if raw is None:
            continue
        s = str(raw).strip().lower()
        if not s or s in seen:
            continue
        s = s[:max_len]
        seen.add(s)
        out.append(s)
        if len(out) >= max_count:
            break
    return out


async def unregister_watchdog(*, cartridge_id: str, slug: str) -> bool:
    if not await _has_table():
        return False
    pool = await auth.pool()
    try:
        res = await pool.execute(
            """
            DELETE FROM copilot_watchdogs
             WHERE cartridge_id = $1 AND slug = $2
            """,
            cartridge_id.strip().lower(), slug.strip().lower(),
        )
    finally:
        invalidate_list_cache()
    return res.endswith("DELETE 1")


_LIST_CACHE_TTL_SECONDS = 60.0
_LIST_CACHE_MAX_ENTRIES = 256
_list_cache: dict[tuple[str | None, bool, int], tuple[float, list[dict[str, Any]]]] = {}


def invalidate_list_cache() -> None:
    _list_cache.clear()


def _normalise_cartridge_id(cartridge_id: str | None) -> str | None:
    if cartridge_id is None:
        return None
    return cartridge_id.strip().lower()


async def list_watchdogs(
    *,
    cartridge_id: str | None = None,
    enabled_only: bool = True,
    limit: int = _MAX_LIST_LIMIT,
) -> list[dict[str, Any]]:
    norm_cartridge = _normalise_cartridge_id(cartridge_id)
    cache_key = (norm_cartridge, enabled_only, limit)
    now = time.monotonic()
    cached = _list_cache.get(cache_key)
    if cached and cached[0] > now:
        return [dict(d) for d in cached[1]]
    if not await _has_table():
        return []
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT id::text         AS id,
               cartridge_id,
               slug,
               name,
               description,
               intent_keywords,
               agent_slug,
               tools,
               risk_level,
               enabled,
               metadata
          FROM copilot_watchdogs
         WHERE ($1::text IS NULL OR cartridge_id = $1)
           AND ($2::boolean = FALSE OR enabled = TRUE)
         ORDER BY cartridge_id, slug
         LIMIT $3
        """,
        norm_cartridge, enabled_only, limit,
    )
    out = [dict(r) for r in rows]
    if len(_list_cache) >= _LIST_CACHE_MAX_ENTRIES:
        _list_cache.clear()
    _list_cache[cache_key] = (now + _LIST_CACHE_TTL_SECONDS, out)
    return [dict(d) for d in out]


async def get_watchdog(*, cartridge_id: str, slug: str) -> dict[str, Any] | None:
    if not await _has_table():
        return None
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        SELECT id::text         AS id,
               cartridge_id,
               slug,
               name,
               description,
               intent_keywords,
               agent_slug,
               tools,
               risk_level,
               enabled,
               metadata
          FROM copilot_watchdogs
         WHERE cartridge_id = $1 AND slug = $2
        """,
        cartridge_id.strip().lower(), slug.strip().lower(),
    )
    return dict(row) if row else None


def _score_watchdog(wd: dict[str, Any], intent_tokens: set[str]) -> float:
    keywords = set(wd.get("intent_keywords") or [])
    if not keywords:
        return 0.0
    overlap = keywords & intent_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(1, len(keywords))


async def relevant_watchdogs(
    intent_text: str, *, cartridge_id: str | None = None,
    min_score: float = 0.1, limit: int = 5,
) -> list[dict[str, Any]]:
    candidates = await list_watchdogs(cartridge_id=cartridge_id)
    if not candidates:
        return []
    intent_tokens = tokenize_intent(intent_text or "")
    if not intent_tokens:
        return []
    scored = [(wd, _score_watchdog(wd, intent_tokens)) for wd in candidates]
    scored = [(wd, s) for wd, s in scored if s >= min_score]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [wd for wd, _ in scored[:limit]]


async def invoke_watchdog(
    *,
    cartridge_id: str,
    slug: str,
    user: dict[str, Any],
    input_text: str,
) -> dict[str, Any]:
    wd = await get_watchdog(cartridge_id=cartridge_id, slug=slug)
    if not wd:
        return {"error": "watchdog_not_found", "watchdog": None}
    if not wd.get("enabled"):
        return {"error": "watchdog_disabled", "watchdog": wd}

    agent_slug = wd.get("agent_slug")
    if agent_slug:
        from app.services import agent_runtime
        agent = await agent_runtime.load_agent_by_slug(
            cartridge_id,
            agent_slug,
            user_context=user,
        )
        if agent is None:
            return {
                "error": "agent_not_found",
                "watchdog": wd,
                "detail": (
                    f"watchdog references agent_slug={agent_slug!r} but "
                    "no row found in agents table for this cartridge"
                ),
            }
        try:
            output = await agent_runtime.run(
                agent=agent,
                message=input_text,
                history=[],
                user=user,
            )
        except Exception as exc:
            logger.warning(
                "watchdog %s/%s agent run failed: %s",
                cartridge_id, slug, exc,
            )
            return {
                "error": "agent_run_failed",
                "watchdog": wd,
                "detail": str(exc)[:500],
            }
        return {"watchdog": wd, "mode": "agent", "agent_run": output}

    return {
        "watchdog": wd,
        "mode": "tool_catalog",
        "tools": list(wd.get("tools") or []),
    }

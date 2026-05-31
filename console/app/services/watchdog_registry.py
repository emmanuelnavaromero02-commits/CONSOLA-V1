"""Sprint v1.45 — watchdog registry (Nivel 4).

Cartridges expose specialised agents — ``forecast_watchdog`` for
HubSpot deals at risk, ``margin_watchdog`` for projects bleeding
billable hours, ``audit_watchdog`` for SAP HCM payroll anomalies.
This registry is the **lookup table** the copilot consults to find
the right specialist for an intent. The actual execution still goes
through ``agent_runtime`` (when ``agent_slug`` is set) or via direct
MCP tool invocation.

The registry is workspace-agnostic on purpose: a watchdog is a
property of the cartridge build, not of the tenant.
"""
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


# ── Public types ──────────────────────────────────────────────────────


VALID_RISK = ("read", "write", "destructive")


# ── Existence guard ────────────────────────────────────────────────────


async def _has_table() -> bool:
    pool = await auth.pool()
    return await has_table_cached(pool, "copilot_watchdogs")


# ── Registration ──────────────────────────────────────────────────────


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
    """Idempotent upsert by (cartridge_id, slug). Returns the row id
    or None if the table isn't present.

    ``intent_keywords`` and ``tools`` are sanitised (truncated, deduped,
    cap at MAX_KEYWORDS) so a malformed cartridge config can't blow up
    the prompt context.
    """
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
        # Audit-round-5 P1 fix: always invalidate the list cache, even
        # if the UPSERT raised. ``ON CONFLICT … DO UPDATE`` can still
        # have committed a partial mutation (e.g. a trigger fired and
        # rolled back) — better to drop the cache than to serve
        # callers a stale view that hides the change they just made.
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
        # Same belt-and-braces as register_watchdog — invalidate even
        # on exception so an aborted delete can't leak via a stale
        # cache entry.
        invalidate_list_cache()
    return res.endswith("DELETE 1")


# ── Lookup ────────────────────────────────────────────────────────────


# ── List cache ────────────────────────────────────────────────────────
#
# briefing_v2 invokes ``relevant_watchdogs`` (which calls
# ``list_watchdogs``) once per highlight. Without caching that's 6×200
# row reads on every dashboard load — measurable latency at 1k
# concurrent users. Cache the per-cartridge result for 60 s; watchdog
# registry rows change at deploy time, not at request time, so a short
# TTL is safe.

_LIST_CACHE_TTL_SECONDS = 60.0
# Audit-round-5 P1 fix: cap the cache so a client iterating
# ``(cartridge_id × enabled_only × limit)`` combinations can't grow
# the dict unbounded. Watchdog rows change at deploy time, not at
# request time, so a small ceiling here is fine — the worst case is
# a couple of extra DB hits after a sweep evicts a hot key.
_LIST_CACHE_MAX_ENTRIES = 256
_list_cache: dict[tuple[str | None, bool, int], tuple[float, list[dict[str, Any]]]] = {}


def invalidate_list_cache() -> None:
    """Drop the in-process list cache. Call from register_watchdog and
    unregister_watchdog so a freshly-edited row doesn't get masked by
    a stale 60s window."""
    _list_cache.clear()


def _normalise_cartridge_id(cartridge_id: str | None) -> str | None:
    """Normalise once so the cache key matches the SQL filter. The
    previous version normalised at SQL bind time but cached under the
    raw input, so callers that bounced between ``"Replicon"`` and
    ``"replicon"`` doubled their cache footprint and missed every hit.
    """
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
        # Audit-round-5 P1 fix: return a defensive shallow copy of the
        # list (and each dict inside) so a downstream mutation by one
        # caller can't poison the cached entry for the next caller.
        # ``list(rows)`` would still share the dict objects.
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
    # Best-effort cap: a single sweeping clear is cheaper than maintaining
    # an LRU order under asyncio. The TTL takes care of the steady state.
    if len(_list_cache) >= _LIST_CACHE_MAX_ENTRIES:
        _list_cache.clear()
    _list_cache[cache_key] = (now + _LIST_CACHE_TTL_SECONDS, out)
    # And hand back a fresh copy so the caller can mutate without
    # corrupting the cached entry.
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


# ── Orchestration: pick the right watchdog for an intent ──────────────


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
    """Return watchdogs whose keywords overlap the intent, ranked by
    overlap ratio. Useful for the goal_solver to decide which
    specialist agent to invoke before falling back to raw tools.

    ``min_score=0.1`` filters out watchdogs whose keywords match less
    than 10% of the intent — empirically that's the floor where the
    suggestion stops being useful and starts being noise. Callers that
    really want every plausibly-related watchdog can override it.
    """
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


# ── Invocation ────────────────────────────────────────────────────────


async def invoke_watchdog(
    *,
    cartridge_id: str,
    slug: str,
    user: dict[str, Any],
    input_text: str,
) -> dict[str, Any]:
    """Run the underlying agent (if ``agent_slug`` is set) or return a
    structured pointer to the tools the watchdog declares.

    This function intentionally does NOT execute destructive tools.
    The copilot's standard approval gate still applies — invoking a
    watchdog is the discovery step, not the action.

    Returns a dict shaped:
      {
        "watchdog": {...registry row...},
        "mode": "agent" | "tool_catalog",
        "agent_run": {...agent_runtime.run output...}   # mode=agent
        "tools":      [...]                              # mode=tool_catalog
      }
    """
    wd = await get_watchdog(cartridge_id=cartridge_id, slug=slug)
    if not wd:
        return {"error": "watchdog_not_found", "watchdog": None}
    if not wd.get("enabled"):
        return {"error": "watchdog_disabled", "watchdog": wd}

    agent_slug = wd.get("agent_slug")
    if agent_slug:
        from app.services import agent_runtime
        agent = await agent_runtime.load_agent_by_slug(
            cartridge_id, agent_slug,
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
            # agent_runtime.run signature: (agent, message: str,
            # history: list[dict] | None = None, user: dict | None = None, ...)
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

    # No agent → return the tool catalog so the caller (typically the
    # goal_solver) can decide what to do with it.
    return {
        "watchdog": wd,
        "mode": "tool_catalog",
        "tools": list(wd.get("tools") or []),
    }

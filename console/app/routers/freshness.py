"""Sprint v1.41.1 — Data freshness service (copilot scaffolding).

Returns watermark timestamps + last extraction status per
(cartridge, entity). The copilot (v1.43) calls
:func:`freshness_for_cartridge_internal` directly — same SQL, no auth
dependency — to annotate citation cards with how stale the cited data
is. Schema used:

  entity_config(cartridge_id, entity, …)
  entity_watermarks(cartridge_id, entity_name, last_watermark_value, updated_at)
  extraction_runs(cartridge_id, entity_name, status, finished_at, started_at)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_authenticated
from app.services import auth


_KNOWN_CARTRIDGES = frozenset({
    "replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors",
})


router = APIRouter(
    prefix="/api/freshness",
    tags=["freshness"],
    dependencies=[Depends(require_authenticated)],
)


# ── Internal API (no auth dependency) ───────────────────────────────────────
# v1.43: callable from copilot_service without an HTTP round-trip. Both
# helpers raise HTTPException on bad input so router thin-wrappers can
# just await them.

async def freshness_for_cartridge_internal(cartridge: str) -> dict:
    """Per-entity freshness for one cartridge. Identical SQL to the
    HTTP endpoint below — the router just wraps this."""
    if cartridge not in _KNOWN_CARTRIDGES:
        raise HTTPException(404, "Unknown cartridge")
    pool = await auth.pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ec.entity                                 AS entity,
                ew.last_watermark_value                   AS watermark_value,
                ew.updated_at                             AS watermark_updated_at,
                er.status                                 AS last_run_status,
                er.finished_at                            AS last_run_at,
                EXTRACT(EPOCH FROM (now() - COALESCE(ew.updated_at, er.finished_at)))::int
                                                          AS age_seconds
            FROM entity_config ec
            LEFT JOIN entity_watermarks ew
                ON  ew.cartridge_id = ec.cartridge_id
                AND ew.entity_name  = ec.entity
            LEFT JOIN LATERAL (
                SELECT status, finished_at
                FROM extraction_runs
                WHERE cartridge_id = ec.cartridge_id
                  AND entity_name  = ec.entity
                -- v1.43.x R1-DBA: removed NULLS LAST. The index
                -- idx_extraction_runs_cartridge_entity_started is
                -- created as (cartridge_id, entity_name, started_at
                -- DESC) which defaults to NULLS FIRST. Using NULLS LAST
                -- on the query prevented PG from doing the index-only
                -- top-1 seek. extraction_runs.started_at is set to
                -- NOW() on every insert (00_schema.sql does not allow
                -- NULL via the upstream codepath), so the NULL-aware
                -- ordering is unnecessary defence in depth.
                ORDER BY started_at DESC
                LIMIT 1
            ) er ON TRUE
            WHERE ec.cartridge_id = $1
            ORDER BY ec.entity
            """,
            cartridge,
        )
    return {"cartridge": cartridge, "entities": [dict(r) for r in rows]}


async def freshness_all_internal() -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ec.cartridge_id,
                COUNT(*)                                                AS entity_count,
                MAX(EXTRACT(EPOCH FROM (now() - ew.updated_at))::int)   AS oldest_age_seconds,
                MIN(EXTRACT(EPOCH FROM (now() - ew.updated_at))::int)   AS newest_age_seconds
            FROM entity_config ec
            LEFT JOIN entity_watermarks ew
                ON  ew.cartridge_id = ec.cartridge_id
                AND ew.entity_name  = ec.entity
            GROUP BY ec.cartridge_id
            ORDER BY ec.cartridge_id
            """
        )
    return {"cartridges": [dict(r) for r in rows]}


# ── HTTP endpoints ──────────────────────────────────────────────────────────

@router.get("/{cartridge}")
async def freshness_for_cartridge(cartridge: str) -> dict:
    return await freshness_for_cartridge_internal(cartridge)


@router.get("")
async def freshness_all() -> dict:
    return await freshness_all_internal()

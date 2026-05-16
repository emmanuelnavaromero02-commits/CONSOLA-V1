"""Sprint v1.41.1 — Data freshness service (copilot scaffolding).

Returns watermark timestamps + last extraction status per
(cartridge, entity). The future copilot (v1.43) uses this to attach a
freshness indicator to every answer it cites — e.g. "1,247 employees
⏱ datos de hace 6h".

Schema used:
  entity_config(cartridge_id, entity, …)
  entity_watermarks(cartridge_id, entity_name, last_watermark_value, updated_at)
  extraction_runs(cartridge_id, entity_name, status, finished_at, started_at)

(See infra/init/00_schema.sql — the SQL in the v1.41.1 plan referenced
``entity_name``/``ended_at`` which don't exist; corrected here.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_authenticated
from app.services import auth


_KNOWN_CARTRIDGES = frozenset({
    "replicon", "sap_hcm", "sap_s4hana", "sap_successfactors",
})


router = APIRouter(
    prefix="/api/freshness",
    tags=["freshness"],
    dependencies=[Depends(require_authenticated)],
)


@router.get("/{cartridge}")
async def freshness_for_cartridge(cartridge: str) -> dict:
    """Per-entity freshness: watermark + last run status + age_seconds."""
    if cartridge not in _KNOWN_CARTRIDGES:
        raise HTTPException(404, "Unknown cartridge")
    pool = await auth.pool()
    async with pool.acquire() as conn:
        # LATERAL pulls the latest run row per entity in one pass instead
        # of a window function over the whole table.
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
                ORDER BY started_at DESC NULLS LAST
                LIMIT 1
            ) er ON TRUE
            WHERE ec.cartridge_id = $1
            ORDER BY ec.entity
            """,
            cartridge,
        )
        return {
            "cartridge": cartridge,
            "entities": [dict(r) for r in rows],
        }


@router.get("")
async def freshness_all() -> dict:
    """Summary across all cartridges (oldest + newest watermark per cartridge).

    Used by the copilot's preamble to know whether it's safe to answer
    without re-running an extraction first.
    """
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

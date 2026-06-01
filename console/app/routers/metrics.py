"""Sprint v1.41.1 — Basic operational metrics endpoint.

Surfaces the counters a human operator wants on /operations:
- extractions last 24h (total / failed)
- average extraction duration last 24h (success only)
- slowest 5 entities by average duration over the last 7d
- audit_events last 24h (admin activity volume)

Charts are intentionally out of scope here — they ship in v1.44
alongside the design system refresh.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services import auth
from app.services.permissions import require_permission


require_operations_read = require_permission("operations.read")

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"],
    dependencies=[Depends(require_operations_read)],
)


@router.get("/operational")
async def operational_metrics() -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        extractions_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM extraction_runs
            WHERE started_at >= now() - INTERVAL '24 hours'
            """
        ) or 0
        errors_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM extraction_runs
            WHERE started_at >= now() - INTERVAL '24 hours'
              AND status = 'failed'
            """
        ) or 0
        avg_duration_sec = await conn.fetchval(
            """
            SELECT AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))
            FROM extraction_runs
            WHERE started_at >= now() - INTERVAL '24 hours'
              AND status = 'success'
              AND finished_at IS NOT NULL
            """
        ) or 0
        slowest = await conn.fetch(
            """
            SELECT cartridge_id, entity_name,
                   AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) AS avg_sec
            FROM extraction_runs
            WHERE started_at >= now() - INTERVAL '7 days'
              AND status = 'success'
              AND finished_at IS NOT NULL
            GROUP BY cartridge_id, entity_name
            ORDER BY avg_sec DESC NULLS LAST
            LIMIT 5
            """
        )
        audit_count_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE created_at >= now() - INTERVAL '24 hours'
            """
        ) or 0
        return {
            "extractions_24h": int(extractions_24h),
            "errors_24h": int(errors_24h),
            "avg_duration_seconds": float(avg_duration_sec or 0),
            "slowest_entities_7d": [dict(r) for r in slowest],
            "audit_events_24h": int(audit_count_24h),
        }

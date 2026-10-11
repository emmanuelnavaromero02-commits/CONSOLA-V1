from __future__ import annotations

from typing import Any


ALERT_WINDOW_DAYS = 7
ROLLUP_ENTITIES = (
    "DatasetRefreshChain",
    "EntityScheduler",
    "AgentRunner",
    "SuccessFactorsDailyExtract",
)
PROCESS_ENTITIES = ("AgentOpsMonitors",)
NON_EXTRACTION_ENTITIES = (*ROLLUP_ENTITIES, *PROCESS_ENTITIES)
ATTENTION_STATUSES = ("failed", "error", "blocked", "partial")

# A mirror row duplicates the DAG row that records the same entity attempt.
MIRROR_DUPLICATE_SQL = """
COALESCE({row}.extra ->> 'source', '') = 'extraction_runs_mirror'
AND EXISTS (
    SELECT 1
      FROM pipeline_runs d
     WHERE d.workspace_id = {row}.workspace_id
       AND d.cartridge_id = {row}.cartridge_id
       AND d.entity = {row}.entity
       AND d.run_id <> {row}.run_id
       AND COALESCE(d.extra ->> 'source', '') <> 'extraction_runs_mirror'
       AND {row}.run_id IN (
            d.extra ->> 'entity_idempotency_key',
            (d.extra ->> 'parent_idempotency_key') || ':' || d.entity,
            d.extra -> 'raw_conf' ->> 'idempotency_key'
       )
)
"""


def mirror_duplicate_predicate(row_alias: str) -> str:
    if row_alias not in {"pr", "pipeline_runs"}:
        raise ValueError("unsupported pipeline_runs alias")
    return MIRROR_DUPLICATE_SQL.replace("{row}", row_alias)


_COUNTED_CTE = (
    """
WITH windowed AS (
    SELECT pr.run_id,
           pr.airflow_dag_run_id,
           pr.cartridge_id,
           pr.entity,
           CASE WHEN pr.status = 'error' THEN 'failed' ELSE pr.status END AS status,
           CASE
               WHEN pr.status IN ('failed', 'error') THEN 3
               WHEN pr.status = 'blocked' THEN 2
               ELSE 1
           END AS severity_rank,
           (left(pr.entity, 2) = '__' OR pr.entity = ANY($4::text[])) AS rollup,
           (pr.entity = ANY($6::text[])) AS process,
           CASE
               WHEN jsonb_typeof(pr.extra -> 'child_run_ids') = 'array'
               THEN pr.extra -> 'child_run_ids'
               ELSE '[]'::jsonb
           END AS child_run_ids
      FROM pipeline_runs pr
     WHERE pr.workspace_id = $1::uuid
       AND ($2::uuid IS NULL OR pr.tenant_id = $2::uuid)
       AND pr.status = ANY($5::text[])
       AND COALESCE(pr.finished_at, pr.started_at)
           >= NOW() - make_interval(days => $3::int)
       AND NOT (
"""
    + mirror_duplicate_predicate("pr")
    + """
       )
),
counted AS (
    SELECT w.*
      FROM windowed w
     WHERE NOT w.rollup
        OR NOT EXISTS (
            SELECT 1
              FROM windowed c
             WHERE NOT c.rollup
               AND NOT c.process
               AND c.severity_rank >= w.severity_rank
               AND c.airflow_dag_run_id IS NOT NULL
               AND (
                    c.airflow_dag_run_id = w.airflow_dag_run_id
                    OR c.airflow_dag_run_id = w.run_id
                    OR w.child_run_ids ? c.airflow_dag_run_id
                    OR w.child_run_ids ? c.run_id
               )
        )
)
"""
)
_GROUPED = """
 GROUP BY c.status, c.cartridge_id, c.entity, c.rollup, c.process
 ORDER BY c.entity, c.status
 LIMIT 200
"""
ATTENTION_SQL = (
    _COUNTED_CTE
    + """
SELECT c.status, c.cartridge_id, c.entity, c.rollup, c.process, COUNT(*)::int AS total
  FROM counted c
 WHERE NOT EXISTS (
        SELECT 1
          FROM entity_config ec
         WHERE ec.cartridge_id = c.cartridge_id
           AND ec.entity = c.entity
           AND ec.enabled IS FALSE
   )
"""
    + _GROUPED
)
ATTENTION_SQL_WITHOUT_ENTITY_CONFIG = (
    _COUNTED_CTE
    + """
SELECT c.status, c.cartridge_id, c.entity, c.rollup, c.process, COUNT(*)::int AS total
  FROM counted c
"""
    + _GROUPED
)


def attention_summary(rows: list[Any]) -> dict[str, Any]:
    totals = {"failed": 0, "blocked": 0, "partial": 0}
    affected: list[dict[str, Any]] = []
    for row in rows:
        status = str(row["status"] or "")
        if status not in totals:
            continue
        total = int(row["total"] or 0)
        totals[status] += total
        entity = str(row["entity"] or "")
        affected.append(
            {
                "status": status,
                "cartridge_id": str(row["cartridge_id"] or ""),
                "entity": entity,
                "rollup": bool(row["rollup"]),
                "process": entity in PROCESS_ENTITIES,
                "total": total,
            }
        )
    return {**totals, "affected": affected}


async def load_attention(
    conn: Any,
    workspace_id: str,
    tenant_id: str | None,
    *,
    days: int = ALERT_WINDOW_DAYS,
) -> dict[str, Any]:
    has_entity_config = bool(
        await conn.fetchval("SELECT to_regclass('public.entity_config')")
    )
    rows = await conn.fetch(
        ATTENTION_SQL if has_entity_config else ATTENTION_SQL_WITHOUT_ENTITY_CONFIG,
        workspace_id,
        tenant_id,
        int(days),
        list(ROLLUP_ENTITIES),
        list(ATTENTION_STATUSES),
        list(PROCESS_ENTITIES),
    )
    return attention_summary(rows)

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_lineage import MAX_LINEAGE_DEPTH
from app.services.control_room.business_serialization import dumps_jsonb


_REPLACED_POLICY_KEYS = (
    "actual_value",
    "affected_count",
    "aggregation_type",
    "analysis_evidence",
    "as_of",
    "count",
    "data_readiness",
    "data_status",
    "denominator",
    "denominator_count",
    "derived_from",
    "detected_at",
    "evaluation_status",
    "evidence",
    "evidence_pack",
    "evidence_refs",
    "intelligence",
    "is_observed",
    "lineage",
    "metric_kind",
    "metric_type",
    "metric_value",
    "observation_date",
    "observation_valid",
    "observed_at",
    "observed_value",
    "parent_item_id",
    "population",
    "population_count",
    "readiness_status",
    "sample_count",
    "source_item_id",
    "source_row_count",
    "source_status",
    "total_count",
    "value",
    "value_observed",
    "value_type",
)


PERSIST_ITEMS_SQL = """
INSERT INTO control_room_items (
    tenant_id, workspace_id, item_id, cartridge_id, domain,
    source_dataset, item_kind, title, severity, status,
    entity_kind, entity_id, entity_label, anomaly_type, metadata,
    impact_estimate, impact_currency, confidence, priority_score,
    selected_option_id, execution_status, first_seen_at, last_seen_at
)
SELECT NULLIF(x.tenant_id, '')::uuid, x.workspace_id::uuid, x.item_id,
       x.cartridge_id, x.domain, x.source_dataset, x.item_kind,
       x.title, x.severity, 'open', x.entity_kind, x.entity_id,
       x.entity_label, x.anomaly_type, x.metadata, x.impact_estimate,
       x.impact_currency, x.confidence, x.priority_score,
       x.selected_option_id, x.execution_status, NOW(), NOW()
  FROM jsonb_to_recordset($1::jsonb) AS x(
       tenant_id text, workspace_id text, item_id text, cartridge_id text,
       domain text, source_dataset text, item_kind text, title text,
       severity text, entity_kind text, entity_id text, entity_label text,
       anomaly_type text, metadata jsonb, impact_estimate numeric,
       impact_currency text, confidence numeric, priority_score integer,
       selected_option_id text, execution_status text
  )
ON CONFLICT (workspace_id, item_id) DO UPDATE
SET cartridge_id = EXCLUDED.cartridge_id,
    domain = EXCLUDED.domain,
    source_dataset = EXCLUDED.source_dataset,
    item_kind = EXCLUDED.item_kind,
    title = EXCLUDED.title,
    severity = EXCLUDED.severity,
    entity_kind = EXCLUDED.entity_kind,
    entity_id = EXCLUDED.entity_id,
    entity_label = EXCLUDED.entity_label,
    anomaly_type = EXCLUDED.anomaly_type,
    metadata = (control_room_items.metadata - $2::text[]) || EXCLUDED.metadata,
    impact_estimate = EXCLUDED.impact_estimate,
    impact_currency = EXCLUDED.impact_currency,
    confidence = EXCLUDED.confidence,
    priority_score = EXCLUDED.priority_score,
    last_seen_at = NOW()
"""


async def persist_item_rows(
    conn: Any,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows:
        return
    await conn.execute(
        PERSIST_ITEMS_SQL,
        dumps_jsonb(list(rows)),
        list(_REPLACED_POLICY_KEYS),
    )


_ITEM_COLUMN_NAMES = (
    "tenant_id",
    "workspace_id",
    "item_id",
    "cartridge_id",
    "domain",
    "source_dataset",
    "item_kind",
    "title",
    "severity",
    "status",
    "decision_id",
    "entity_kind",
    "entity_id",
    "entity_label",
    "anomaly_type",
    "metadata",
    "first_seen_at",
    "last_seen_at",
    "resolved_at",
    "dismissed_at",
    "impact_estimate",
    "impact_currency",
    "confidence",
    "priority_score",
    "selected_option_id",
    "execution_status",
)


def _columns(alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{name}" for name in _ITEM_COLUMN_NAMES)


async def fetch_lineage_rows(
    conn: Any,
    *,
    workspace_id: str,
    parent_ids: Sequence[str],
    tenant_id: str | None = None,
    owner_id: int | None = None,
) -> list[Any]:
    roots = sorted({str(value).strip() for value in parent_ids if str(value).strip()})
    if not roots:
        return []
    params: list[Any] = [workspace_id, roots]
    seed_scope: list[str] = []
    parent_scope: list[str] = []
    if tenant_id:
        params.append(tenant_id)
        seed_scope.append(f"AND seed.tenant_id::text = ${len(params)}")
        parent_scope.append(f"AND parent.tenant_id::text = ${len(params)}")
    if owner_id is not None:
        params.append(owner_id)
        seed_scope.append(f"AND seed.owner_user_id = ${len(params)}")
        parent_scope.append(f"AND parent.owner_user_id = ${len(params)}")
    params.append(MAX_LINEAGE_DEPTH)
    max_depth_param = f"${len(params)}"
    return await conn.fetch(
        f"""
        WITH RECURSIVE lineage AS (
            SELECT {_columns('seed')}, ARRAY[seed.item_id]::text[] AS path, 0 AS depth
              FROM control_room_items seed
             WHERE seed.workspace_id = $1
               AND seed.item_id = ANY($2::text[])
               {' '.join(seed_scope)}
            UNION ALL
            SELECT {_columns('parent')},
                   lineage.path || parent.item_id, lineage.depth + 1
              FROM lineage
              CROSS JOIN LATERAL (
                  SELECT direct.parent_id
                    FROM (VALUES
                        (lineage.metadata->>'parent_item_id'),
                        (lineage.metadata->>'source_item_id'),
                        (CASE
                            WHEN jsonb_typeof(lineage.metadata->'derived_from') = 'string'
                            THEN lineage.metadata->>'derived_from'
                         END),
                        (lineage.metadata->'derived_from'->>'item_id'),
                        (lineage.metadata->'derived_from'->>'id'),
                        (lineage.metadata->'lineage'->>'parent_item_id'),
                        (lineage.metadata->'lineage'->>'source_item_id')
                    ) direct(parent_id)
                  UNION ALL
                  SELECT CASE jsonb_typeof(entry.value)
                             WHEN 'string' THEN entry.value #>> '{{}}'
                             WHEN 'object' THEN COALESCE(
                                 entry.value->>'item_id', entry.value->>'id'
                             )
                         END
                    FROM jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(lineage.metadata->'derived_from') = 'array'
                            THEN lineage.metadata->'derived_from'
                            ELSE '[]'::jsonb
                        END
                    ) entry(value)
              ) refs(parent_id)
              JOIN control_room_items parent
                ON parent.workspace_id = $1
               AND parent.item_id = refs.parent_id
             WHERE refs.parent_id IS NOT NULL
               AND refs.parent_id <> ''
               AND lineage.depth < {max_depth_param}
               AND NOT parent.item_id = ANY(lineage.path)
               {' '.join(parent_scope)}
        )
        SELECT DISTINCT ON (item_id) {_columns()}
          FROM lineage
         ORDER BY item_id, depth
        """,
        *params,
    )


def decision_provenance(
    origin: str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "type": "decision_provenance",
        "version": 1,
        "origin": str(origin).strip().lower(),
    }
    if item_id:
        provenance["item_id"] = str(item_id)
    return {
        "label": "Provenance",
        "value": provenance["origin"],
        "provenance": provenance,
    }


def decision_kpis_with_provenance(
    value: Any,
    origin: str,
    *,
    item_id: str | None = None,
) -> list[Any]:
    kpis = list(value) if isinstance(value, list) else []
    kpis.append(decision_provenance(origin, item_id=item_id))
    return kpis


async def link_control_room_decision(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    decision_id: int,
) -> None:
    linked = await conn.fetchrow(
        """
        UPDATE control_room_items
           SET status = 'decision_created',
               decision_id = $1,
               metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
         RETURNING item_id
        """,
        decision_id,
        workspace_id,
        item_id,
        dumps_jsonb(
            {
                "decision_provenance": {
                    "version": 1,
                    "origin": "control_room",
                    "decision_id": decision_id,
                    "item_id": item_id,
                }
            }
        ),
    )
    if not linked or str(linked["item_id"]) != str(item_id):
        raise RuntimeError("control room decision link was not persisted")


def parse_command_tag(result: Any, command: str) -> int:
    text = str(result or "").strip()
    parts = text.split()
    if len(parts) < 2 or parts[0].upper() != command.upper():
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0

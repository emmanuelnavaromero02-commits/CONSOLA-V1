from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.domains.decisions.provenance import (
    decision_kpis_with_provenance,
    decision_provenance,
)
from app.services.control_room.business_item_persistence import (
    parse_command_tag,
    persist_item_rows,
)
from app.services.control_room.business_lineage import MAX_LINEAGE_DEPTH
from app.services.control_room.business_serialization import dumps_jsonb
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
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


async def link_control_room_decision(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    decision_id: int,
    owner_user_id: int | None,
    item: Mapping[str, Any],
) -> None:
    await _set_control_room_decision_link(
        conn,
        workspace_id=workspace_id,
        item_id=item_id,
        decision_id=decision_id,
        owner_user_id=owner_user_id,
        item=item,
        status="decision_created",
        resolved=False,
        extra_metadata={},
        stage=WorkflowStage.DECISION_CREATED,
    )


async def approve_control_room_decision(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    decision_id: int,
    owner_user_id: int | None,
    item: Mapping[str, Any],
    lessons: Sequence[str],
) -> None:
    await _set_control_room_decision_link(
        conn,
        workspace_id=workspace_id,
        item_id=item_id,
        decision_id=decision_id,
        owner_user_id=owner_user_id,
        item=item,
        status="approved",
        resolved=True,
        extra_metadata={"lessons": list(lessons)},
        stage=WorkflowStage.APPROVED,
    )


async def _set_control_room_decision_link(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    decision_id: int,
    owner_user_id: int | None,
    item: Mapping[str, Any],
    status: str,
    resolved: bool,
    extra_metadata: Mapping[str, Any],
    stage: WorkflowStage,
) -> None:
    current = await conn.fetchrow(
        """SELECT item_id, owner_user_id, decision_id
             FROM control_room_items
            WHERE workspace_id = $1 AND item_id = $2
            FOR UPDATE""",
        workspace_id,
        item_id,
    )
    if not current or current.get("owner_user_id") != owner_user_id:
        raise HTTPException(404, "control room item not found")
    linked_decision_id = current.get("decision_id")
    if stage is WorkflowStage.APPROVED and linked_decision_id != decision_id:
        raise HTTPException(409, "decision is not linked to control room item")
    if (
        stage is WorkflowStage.DECISION_CREATED
        and linked_decision_id is not None
        and linked_decision_id != decision_id
    ):
        raise HTTPException(409, "control room item already has another decision")
    provenance = workflow_eligibility_provenance(
        {**dict(item), "workspace_id": workspace_id},
        stage=stage,
        workspace_id=workspace_id,
        decision_id=decision_id,
        option_id=str(item.get("selected_option_id") or "") or None,
    )
    metadata = {
        **dict(extra_metadata),
        "decision_provenance": {
            "version": 1,
            "origin": "control_room",
            "decision_id": decision_id,
            "item_id": item_id,
        },
        DECISION_PROVENANCE_KEY: provenance,
    }
    linked = await conn.fetchrow(
        """
        UPDATE control_room_items
           SET status = $6,
               decision_id = $1,
               metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
               resolved_at = CASE
                   WHEN $7::boolean THEN COALESCE(resolved_at, NOW())
                   ELSE resolved_at
               END,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $5
           AND (decision_id IS NULL OR decision_id = $1)
         RETURNING item_id
        """,
        decision_id,
        workspace_id,
        item_id,
        dumps_jsonb(metadata),
        owner_user_id,
        status,
        resolved,
    )
    if not linked or str(linked["item_id"]) != str(item_id):
        raise RuntimeError("control room decision link was not persisted")

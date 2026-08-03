from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


async def durable_wisdom_exists(conn: Any, workspace_id: str, source_id: str) -> bool:
    if source_id.strip().upper() != "WB-TALENTO":
        return False
    row = await conn.fetchrow(
        """
        SELECT 1 AS trusted FROM control_room_items
         WHERE workspace_id = $1 AND item_kind = 'agent_alert'
           AND metadata->>'origin' = 'wisdom_bit'
           AND metadata->>'agent_run_id' IS NOT NULL
           AND metadata->>'engine_run_id' IS NOT NULL
           AND metadata->'analysis_evidence'->>'engine' = 'wisdom_bit'
           AND metadata->'analysis_evidence'->>'engine_run_id'
               = metadata->>'engine_run_id'
           AND jsonb_typeof(metadata->'evidence_refs') = 'array'
           AND jsonb_array_length(metadata->'evidence_refs') > 0
         ORDER BY updated_at DESC NULLS LAST LIMIT 1
        """,
        workspace_id,
    )
    return row is not None


async def load_server_wisdom_source(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    owner_id: int | None,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT tenant_id, workspace_id, owner_user_id, item_id, title,
               severity, status, domain, cartridge_id, source_dataset,
               metadata, confidence, updated_at
          FROM control_room_items
         WHERE workspace_id = $1
           AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
           AND ($3::bigint IS NULL OR owner_user_id = $3)
           AND item_kind = 'agent_alert'
           AND metadata->>'origin' = 'wisdom_bit'
           AND metadata->>'engine_run_id' IS NOT NULL
           AND metadata->'analysis_evidence'->>'engine' = 'wisdom_bit'
         ORDER BY updated_at DESC NULLS LAST
         LIMIT 1
        """,
        workspace_id,
        tenant_id,
        owner_id,
    )
    if not row:
        return None
    data = dict(row)
    metadata = _object(data.get("metadata"))
    analysis = _object(metadata.get("analysis_evidence"))
    run_id = str(metadata.get("engine_run_id") or "")
    evidence_refs = metadata.get("evidence_refs")
    if (
        analysis.get("engine") != "wisdom_bit"
        or str(analysis.get("engine_run_id") or "") != run_id
        or not run_id
        or not metadata.get("agent_run_id")
        or not isinstance(evidence_refs, list)
        or not evidence_refs
    ):
        return None
    data.update(
        source_id="WB-TALENTO",
        description=str(metadata.get("description") or ""),
        metadata={
            "metrics": _object(analysis.get("metrics")),
            "entities": [],
            "constraints": {
                "recommendation_only": True,
                "no_external_writeback": True,
            },
            "evidence_refs": evidence_refs,
            "time_horizon": None,
            "provenance": {
                "source": "control_room_agent_alert",
                "item_id": str(data.get("item_id") or ""),
                "agent_run_id": str(metadata["agent_run_id"]),
                "engine_run_id": run_id,
            },
        },
    )
    return data


def server_wisdom_payload(source: dict[str, Any]) -> dict[str, Any]:
    metadata = _object(source.get("metadata"))
    return {
        "title": source.get("title") or "WB-TALENTO",
        "description": source.get("description") or "",
        "metrics": metadata.get("metrics") or {},
        "entities": metadata.get("entities") or [],
        "constraints": metadata.get("constraints") or {},
        "evidence_refs": metadata.get("evidence_refs") or [],
        "time_horizon": metadata.get("time_horizon"),
    }


__all__ = (
    "durable_wisdom_exists",
    "load_server_wisdom_source",
    "server_wisdom_payload",
)

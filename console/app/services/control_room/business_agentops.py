from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


async def _agentops_alert_rows(
    conn: Any,
    *,
    workspace_id: str,
    allowed_param: list[str] | None,
    eligible_alert_ids: Sequence[str],
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT metadata->>'agent_id' AS agent_id,
               COUNT(*)::int AS total,
               COUNT(*) FILTER (WHERE status = 'open')::int AS open,
               MAX(last_seen_at) AS last_seen_at
          FROM control_room_items
         WHERE workspace_id = $1::uuid
           AND item_kind = 'agent_alert'
           AND item_id = ANY($3::text[])
           AND ($2::text[] IS NULL OR cartridge_id = ANY($2::text[]) OR cartridge_id = 'platform')
         GROUP BY metadata->>'agent_id'
        """,
        workspace_id,
        allowed_param,
        list(eligible_alert_ids),
    )


async def _agentops_origin_rows(
    conn: Any,
    *,
    workspace_id: str,
    allowed_param: list[str] | None,
    eligible_alert_ids: Sequence[str],
) -> list[Any]:
    return await conn.fetch(
        """
        SELECT COALESCE(metadata->>'origin', metadata->'analysis_evidence'->>'engine', metadata->>'source', 'unknown') AS origin,
               COUNT(*)::int AS total
          FROM control_room_items
         WHERE workspace_id = $1::uuid
           AND item_kind = 'agent_alert'
           AND item_id = ANY($3::text[])
           AND ($2::text[] IS NULL OR cartridge_id = ANY($2::text[]) OR cartridge_id = 'platform')
         GROUP BY 1
         ORDER BY 2 DESC, 1
        """,
        workspace_id,
        allowed_param,
        list(eligible_alert_ids),
    )


async def _agentops_orchestration_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: Mapping[str, bool],
    eligible_item_ids: Sequence[str],
) -> list[Any]:
    if not table_exists["decision_orchestration_runs"]:
        return []
    return await conn.fetch(
        """
        SELECT COUNT(*)::int AS total,
               MAX(updated_at) AS latest_at
          FROM decision_orchestration_runs
         WHERE workspace_id = $1::uuid
           AND (
                source_type NOT IN (
                    'control_room_item', 'agent_alert', 'intelligence_signal'
                )
                OR source_id = ANY($2::text[])
           )
        """,
        workspace_id,
        list(eligible_item_ids),
    )


async def _agentops_execution_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: Mapping[str, bool],
    eligible_item_ids: Sequence[str],
) -> list[Any]:
    if not table_exists["decision_orchestration_executions"]:
        return []
    return await conn.fetch(
        """
        SELECT execution.engine_name,
               execution.execution_status,
               COUNT(*)::int AS total,
               MAX(execution.updated_at) AS latest_at
          FROM decision_orchestration_executions execution
          JOIN decision_orchestration_runs run
            ON run.workspace_id = execution.workspace_id
           AND run.orchestration_id = execution.orchestration_id
         WHERE execution.workspace_id = $1::uuid
           AND (
                run.source_type NOT IN (
                    'control_room_item', 'agent_alert', 'intelligence_signal'
                )
                OR run.source_id = ANY($2::text[])
           )
         GROUP BY execution.engine_name, execution.execution_status
        """,
        workspace_id,
        list(eligible_item_ids),
    )


__all__ = (
    "_agentops_alert_rows",
    "_agentops_execution_rows",
    "_agentops_orchestration_rows",
    "_agentops_origin_rows",
)

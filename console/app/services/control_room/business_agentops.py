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


async def _agentops_monte_carlo_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: Mapping[str, bool],
    eligible_item_ids: Sequence[str],
) -> list[Any]:
    if not table_exists["monte_carlo_simulations"]:
        return []
    return await conn.fetch(
        """
        SELECT simulation.source_type,
               COUNT(*)::int AS total,
               MAX(simulation.updated_at) AS latest_at
          FROM monte_carlo_simulations simulation
         WHERE simulation.workspace_id = $1::uuid
           AND (
                (
                    simulation.source_type = 'signal'
                    AND simulation.source_id = ANY($2::text[])
                )
                OR (
                    simulation.source_type = 'decision_option'
                    AND EXISTS (
                        SELECT 1
                          FROM decision_options option
                         WHERE option.workspace_id = simulation.workspace_id
                           AND (
                                option.id::text = simulation.source_id
                                OR option.option_id = simulation.source_id
                           )
                           AND option.signal_id = ANY($2::text[])
                    )
                )
           )
         GROUP BY simulation.source_type
        """,
        workspace_id,
        list(eligible_item_ids),
    )


async def _agentops_calibration_rows(
    conn: Any,
    *,
    workspace_id: str,
    table_exists: Mapping[str, bool],
) -> list[Any]:
    if not table_exists["calibration_states"]:
        return []
    return await conn.fetch(
        """
        SELECT COUNT(*)::int AS total,
               COALESCE(SUM(sample_count), 0)::int AS sample_count,
               MAX(updated_at) AS latest_at
          FROM calibration_states
         WHERE workspace_id = $1::uuid
        """,
        workspace_id,
    )


def _agentops_calibration_projection(rows: Sequence[Any]) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    if rows:
        row = dict(rows[0])
        latest_at = row.get("latest_at")
        diagnostics.append(
            {
                "diagnostic": "bayesian_calibration_global",
                "scope": "workspace",
                "state_count": int(row.get("total") or 0),
                "sample_count": int(row.get("sample_count") or 0),
                "latest_at": latest_at.isoformat() if latest_at else None,
                "included_in_business_counters": False,
            }
        )
    return {
        "calibration_total": 0,
        "calibration_samples": 0,
        "calibration_latest": None,
        "operational_diagnostics": diagnostics,
    }


__all__ = (
    "_agentops_alert_rows",
    "_agentops_calibration_projection",
    "_agentops_calibration_rows",
    "_agentops_execution_rows",
    "_agentops_monte_carlo_rows",
    "_agentops_orchestration_rows",
    "_agentops_origin_rows",
)

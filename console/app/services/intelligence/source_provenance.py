from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_orchestrator import eligible_orchestrator_source
from app.services.intelligence.wisdom_source import durable_wisdom_exists
from app.services.intelligence.source_provenance_policy import (
    backtest_policy,
    manual_marker as _manual,
    metadata as _metadata,
    observed_signal as _observed_signal,
    prediction_outcome_observed,
)


MAX_PROVENANCE_DEPTH = 12


@dataclass(frozen=True)
class ProvenanceResult:
    trusted: bool
    reason: str
    depth: int


async def _resolve(
    conn: Any,
    workspace_id: str,
    source_type: str,
    source_id: str,
    *,
    allow_manual: bool,
    visited: set[tuple[str, str]],
    depth: int,
) -> ProvenanceResult:
    key = (source_type, source_id)
    if depth > MAX_PROVENANCE_DEPTH:
        return ProvenanceResult(False, "provenance_depth_exceeded", depth)
    if key in visited:
        return ProvenanceResult(False, "provenance_cycle", depth)
    if not source_type or not source_id:
        return ProvenanceResult(False, "provenance_incomplete", depth)
    visited.add(key)

    if source_type == "manual_fixture":
        return ProvenanceResult(
            allow_manual, "manual_fixture" if allow_manual else "manual_ancestor", depth
        )
    if source_type == "wisdom_bit":
        trusted = await durable_wisdom_exists(conn, workspace_id, source_id)
        return ProvenanceResult(
            trusted,
            "server_wisdom_bit" if trusted else "wisdom_provenance_incomplete",
            depth,
        )
    if source_type in {"control_room_item", "agent_alert"}:
        item_kind_clause = (
            "AND item_kind = 'agent_alert'" if source_type == "agent_alert" else ""
        )
        row = await conn.fetchrow(
            f"""
            SELECT tenant_id, workspace_id, owner_user_id, item_id, item_kind,
                   title, severity, status, domain, cartridge_id, source_dataset,
                   entity_kind, entity_id, entity_label, anomaly_type, metadata
              FROM control_room_items
             WHERE workspace_id = $1 AND item_id = $2 {item_kind_clause}
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        metadata = _metadata(data.get("metadata"))
        if data and (
            _manual(data.get("source_dataset"))
            or _manual(data.get("cartridge_id"))
            or _manual(metadata.get("source_type"))
            or _manual(metadata.get("source_system"))
            or _manual(metadata.get("source_dataset"))
            or metadata.get("input_classification") == "scenario_assumption"
            or metadata.get("observed") is False
        ):
            return ProvenanceResult(False, "manual_ancestor", depth)
        eligible = (
            await eligible_orchestrator_source(
                conn,
                data,
                source_type=source_type,
                tenant_id=str(data.get("tenant_id") or "") or None,
                workspace_id=workspace_id,
                owner_id=data.get("owner_user_id"),
            )
            if data
            else None
        )
        return ProvenanceResult(
            eligible is not None,
            "observed_control_room_item"
            if eligible
            else "control_room_provenance_incomplete",
            depth,
        )
    if source_type in {"signal", "intelligence_signal"}:
        row = await conn.fetchrow(
            """
            SELECT signal_subtype,
                   metadata->>'source_system' AS source_system,
                   metadata->>'source_dataset' AS source_dataset,
                   metadata->>'evidence_pack_id' AS evidence_pack_id,
                   metadata
              FROM intelligence_signals
             WHERE workspace_id = $1 AND signal_id = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        trusted = _observed_signal(row)
        return ProvenanceResult(
            trusted,
            "observed_signal" if trusted else "signal_provenance_incomplete",
            depth,
        )
    if source_type == "decision_option":
        row = await conn.fetchrow(
            """
            SELECT signal_id FROM decision_options
             WHERE workspace_id = $1 AND (id::text = $2 OR option_id = $2)
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        parent = str(dict(row).get("signal_id") or "") if row else ""
        return await _resolve(
            conn,
            workspace_id,
            "signal",
            parent,
            allow_manual=allow_manual,
            visited=visited,
            depth=depth + 1,
        )
    if source_type == "monte_carlo_simulation":
        return ProvenanceResult(False, "scenario_assumption", depth)
    if source_type == "calibration_observation":
        row = await conn.fetchrow(
            "SELECT source_type, source_id FROM calibration_observations WHERE workspace_id = $1 AND (observation_id = $2 OR id::text = $2) LIMIT 1",
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        return await _resolve(
            conn,
            workspace_id,
            str(data.get("source_type") or ""),
            str(data.get("source_id") or ""),
            allow_manual=allow_manual,
            visited=visited,
            depth=depth + 1,
        )
    if source_type == "prediction_outcome":
        row = await conn.fetchrow(
            """SELECT signal_id, option_id, action_taken, actual_value,
                      outcome_summary, owner_user_id, metadata, created_at
                 FROM prediction_outcomes
                WHERE workspace_id = $1 AND id::text = $2 LIMIT 1""",
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        if not prediction_outcome_observed(data):
            return ProvenanceResult(
                False, "prediction_outcome_provenance_incomplete", depth
            )
        if data.get("option_id") and not await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM decision_options
                 WHERE workspace_id=$1 AND (id::text=$2 OR option_id=$2)
                   AND signal_id=$3)""",
            workspace_id,
            str(data["option_id"]),
            str(data["signal_id"]),
        ):
            return ProvenanceResult(False, "prediction_outcome_signal_mismatch", depth)
        return await _resolve(
            conn,
            workspace_id,
            "signal",
            str(data.get("signal_id") or ""),
            allow_manual=allow_manual,
            visited=visited,
            depth=depth + 1,
        )
    if source_type == "backtest_case":
        exists = await conn.fetchval("SELECT to_regclass('public.backtest_results')")
        if not exists:
            return ProvenanceResult(False, "backtest_table_missing", depth)
        row = await conn.fetchrow(
            """
            SELECT result.label_source, result.actual_label, result.result,
                   run.run_mode, run.status, run.completed_at,
                   run.source_system, run.source_dataset, run.labels_available,
                   run.labels_required, run.insufficient_labeled_data
              FROM backtest_results result JOIN backtest_runs run
                ON run.workspace_id = result.workspace_id AND run.id = result.backtest_run_id
             WHERE result.workspace_id = $1 AND result.id::text = $2 LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        policy, outcome_id = backtest_policy(data)
        if policy == "outcome":
            return await _resolve(
                conn,
                workspace_id,
                "prediction_outcome",
                str(outcome_id or ""),
                allow_manual=allow_manual,
                visited=visited,
                depth=depth + 1,
            )
        trusted = policy == "historical"
        return ProvenanceResult(
            trusted,
            "observed_backtest" if trusted else "backtest_provenance_incomplete",
            depth,
        )
    return ProvenanceResult(False, "unsupported_provenance_source", depth)


async def resolve_source_provenance(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    allow_manual: bool = False,
) -> ProvenanceResult:
    return await _resolve(
        conn,
        workspace_id,
        str(source_type or "").strip(),
        str(source_id or "").strip(),
        allow_manual=allow_manual,
        visited=set(),
        depth=0,
    )


async def source_is_trusted(
    conn: Any,
    workspace_id: str,
    source_type: str,
    source_id: str,
    allow_manual: bool,
) -> bool:
    return (
        await resolve_source_provenance(
            conn,
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id,
            allow_manual=allow_manual,
        )
    ).trusted

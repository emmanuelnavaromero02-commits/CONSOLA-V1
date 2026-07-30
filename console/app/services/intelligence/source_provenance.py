from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_orchestrator import eligible_orchestrator_source
from app.services.intelligence.wisdom_source import durable_wisdom_exists


MANUAL_MARKERS = ("fixture", "manual", "mock", "synthetic")
MAX_PROVENANCE_DEPTH = 12


@dataclass(frozen=True)
class ProvenanceResult:
    trusted: bool
    reason: str
    depth: int


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _manual(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in MANUAL_MARKERS)


def _observed_signal(row: Any) -> bool:
    if not row:
        return False
    data = dict(row)
    metadata = _metadata(data.get("metadata"))
    source_system = data.get("source_system") or metadata.get("source_system")
    source_dataset = data.get("source_dataset") or metadata.get("source_dataset")
    evidence_pack = data.get("evidence_pack_id") or metadata.get("evidence_pack_id")
    return bool(
        data.get("signal_subtype") == "observed"
        and source_system
        and source_dataset
        and evidence_pack
        and not _manual(source_system)
        and not _manual(source_dataset)
        and not _manual(metadata.get("source_type"))
        and metadata.get("input_classification") != "scenario_assumption"
        and metadata.get("observed") is not False
    )


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
    if source_type in {"monte_carlo_simulation", "calibration_observation"}:
        table = (
            "monte_carlo_simulations"
            if source_type.startswith("monte")
            else "calibration_observations"
        )
        id_clause = (
            "simulation_id = $2"
            if source_type.startswith("monte")
            else "(observation_id = $2 OR id::text = $2)"
        )
        row = await conn.fetchrow(
            f"SELECT source_type, source_id FROM {table} WHERE workspace_id = $1 AND {id_clause} LIMIT 1",
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
            "SELECT signal_id, metadata FROM prediction_outcomes WHERE workspace_id = $1 AND id::text = $2 LIMIT 1",
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        metadata = _metadata(data.get("metadata"))
        if _manual(metadata.get("source_type")):
            return ProvenanceResult(False, "manual_ancestor", depth)
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
            SELECT result.label_source, run.run_mode, run.status, run.completed_at,
                   run.source_system, run.source_dataset
              FROM backtest_results result JOIN backtest_runs run
                ON run.workspace_id = result.workspace_id AND run.id = result.backtest_run_id
             WHERE result.workspace_id = $1 AND result.id::text = $2 LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        data = dict(row) if row else {}
        trusted = bool(
            data.get("label_source") not in {None, "fixture", "unavailable"}
            and data.get("run_mode") in {"historical_replay", "outcome_linked"}
            and data.get("status") in {"ok", "insufficient_labeled_data"}
            and data.get("completed_at") is not None
            and data.get("source_system")
            and data.get("source_dataset")
            and not _manual(data.get("source_system"))
            and not _manual(data.get("source_dataset"))
        )
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

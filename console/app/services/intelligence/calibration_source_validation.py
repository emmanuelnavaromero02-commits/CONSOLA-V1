from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


MANUAL_MARKERS = ("fixture", "manual", "mock", "synthetic")


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


def _is_manual(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in MANUAL_MARKERS)


def _observed_signal(row: Any) -> bool:
    if not row:
        return False
    data = dict(row)
    metadata = _metadata(data.get("metadata"))
    source_system = data.get("source_system") or metadata.get("source_system")
    source_dataset = data.get("source_dataset") or metadata.get("source_dataset")
    evidence_pack_id = data.get("evidence_pack_id") or metadata.get("evidence_pack_id")
    return bool(
        data.get("signal_subtype") == "observed"
        and source_system
        and source_dataset
        and evidence_pack_id
        and not _is_manual(source_system)
        and not _is_manual(source_dataset)
        and not _is_manual(metadata.get("source_type"))
        and metadata.get("input_classification") != "scenario_assumption"
        and metadata.get("observed") is not False
    )


async def _signal_exists(conn: Any, workspace_id: str, source_id: str) -> bool:
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
    return _observed_signal(row)


async def _decision_option_exists(conn: Any, workspace_id: str, source_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT signal.signal_subtype,
               signal.metadata->>'source_system' AS source_system,
               signal.metadata->>'source_dataset' AS source_dataset,
               signal.metadata->>'evidence_pack_id' AS evidence_pack_id,
               signal.metadata
          FROM decision_options AS option
          JOIN intelligence_signals AS signal
            ON signal.workspace_id = option.workspace_id
           AND signal.signal_id = option.signal_id
         WHERE option.workspace_id = $1
           AND (option.id::text = $2 OR option.option_id = $2)
         LIMIT 1
        """,
        workspace_id,
        source_id,
    )
    return _observed_signal(row)


async def _backtest_exists(conn: Any, workspace_id: str, source_id: str) -> bool:
    exists = await conn.fetchval("SELECT to_regclass('public.backtest_results')")
    if not exists:
        return False
    row = await conn.fetchrow(
        """
        SELECT result.label_source, run.run_mode, run.status, run.completed_at,
               run.source_system, run.source_dataset
          FROM backtest_results AS result
          JOIN backtest_runs AS run
            ON run.workspace_id = result.workspace_id
           AND run.id = result.backtest_run_id
         WHERE result.workspace_id = $1 AND result.id::text = $2
         LIMIT 1
        """,
        workspace_id,
        source_id,
    )
    if not row:
        return False
    data = dict(row)
    return bool(
        data.get("label_source") not in {"fixture", "unavailable"}
        and data.get("run_mode") in {"historical_replay", "outcome_linked"}
        and data.get("status") in {"ok", "insufficient_labeled_data"}
        and data.get("completed_at") is not None
        and data.get("source_system")
        and data.get("source_dataset")
        and not _is_manual(data.get("source_system"))
        and not _is_manual(data.get("source_dataset"))
    )


async def _simulation_exists(conn: Any, workspace_id: str, source_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT source_type, source_id
          FROM monte_carlo_simulations
         WHERE workspace_id = $1 AND simulation_id = $2
         LIMIT 1
        """,
        workspace_id,
        source_id,
    )
    if not row:
        return False
    data = dict(row)
    nested_type = str(data.get("source_type") or "")
    nested_id = str(data.get("source_id") or "")
    if nested_type == "signal":
        return await _signal_exists(conn, workspace_id, nested_id)
    if nested_type == "decision_option":
        return await _decision_option_exists(conn, workspace_id, nested_id)
    if nested_type == "backtest_case":
        return await _backtest_exists(conn, workspace_id, nested_id)
    if nested_type == "wisdom_bit":
        return nested_id.strip().upper() == "WB-TALENTO"
    return False


async def calibration_source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    if source_type == "manual_fixture":
        return True
    if source_type == "monte_carlo_simulation":
        return await _simulation_exists(conn, workspace_id, source_id)
    if source_type == "decision_option":
        return await _decision_option_exists(conn, workspace_id, source_id)
    if source_type == "prediction_outcome":
        row = await conn.fetchrow(
            """
            SELECT signal_id, metadata
              FROM prediction_outcomes
             WHERE workspace_id = $1 AND id::text = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        if not row or _is_manual(
            _metadata(dict(row).get("metadata")).get("source_type")
        ):
            return False
        return await _signal_exists(conn, workspace_id, str(dict(row)["signal_id"]))
    if source_type == "backtest_case":
        return await _backtest_exists(conn, workspace_id, source_id)
    return False


__all__ = ("calibration_source_exists",)

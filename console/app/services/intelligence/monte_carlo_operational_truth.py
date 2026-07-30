from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from app.services.intelligence import monte_carlo


LOCAL_ENVIRONMENTS = {"test", "local", "development"}
MANUAL_MARKERS = {"fixture", "manual", "manual_fixture", "mock", "synthetic"}


def synthetic_allowed() -> bool:
    app_env = os.environ.get("APP_ENV")
    return bool(app_env and app_env.strip().lower() in LOCAL_ENVIRONMENTS)


def normalize_scenario_assumptions(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("assumptions must be an object")
    normalized = dict(value)
    normalized.update(
        input_classification="scenario_assumption",
        observed=False,
        calibration_status="not_calibrated",
    )
    return normalized


def normalize_option_assumptions(
    value: Any,
    base_assumptions: dict[str, Any],
    *,
    inherit_missing: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("options must be a list")
    normalized: list[dict[str, Any]] = []
    option_ids: set[str] = set()
    for option in value:
        if not isinstance(option, dict):
            raise ValueError("each option must be an object")
        option_id = str(option.get("option_id") or "").strip()
        if not option_id:
            raise ValueError("option_id is required")
        if option_id in option_ids:
            raise ValueError("option_id must be unique")
        option_ids.add(option_id)
        clean_option = dict(option)
        if "input_variables" in option and (
            not isinstance(option["input_variables"], dict)
            or not option["input_variables"]
        ):
            raise ValueError("option input_variables must be a non-empty object")
        if "assumptions" in option or inherit_missing:
            assumptions = option.get("assumptions", base_assumptions)
            clean_option["assumptions"] = normalize_scenario_assumptions(assumptions)
        normalized.append(clean_option)
    return normalized


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


def _is_manual_marker(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(marker in text for marker in MANUAL_MARKERS)


def _observed_source_row(row: Any) -> bool:
    if not row:
        return False
    data = dict(row)
    metadata = _metadata(data.get("metadata"))
    source_system = data.get("source_system") or metadata.get("source_system")
    source_dataset = data.get("source_dataset") or metadata.get("source_dataset")
    evidence_pack_id = data.get("evidence_pack_id") or metadata.get("evidence_pack_id")
    if not source_system or not source_dataset or not evidence_pack_id:
        return False
    if _is_manual_marker(source_system) or _is_manual_marker(source_dataset):
        return False
    if _is_manual_marker(metadata.get("source_type")):
        return False
    if metadata.get("input_classification") == "scenario_assumption":
        return False
    if metadata.get("observed") is False:
        return False
    return str(data.get("signal_subtype") or "").strip() == "observed"


def _real_backtest_row(row: Any) -> bool:
    if not row:
        return False
    data = dict(row)
    if data.get("run_mode") not in {"historical_replay", "outcome_linked"}:
        return False
    if data.get("status") not in {"ok", "insufficient_labeled_data"}:
        return False
    if data.get("completed_at") is None:
        return False
    source_system = data.get("source_system")
    source_dataset = data.get("source_dataset")
    return bool(
        source_system
        and source_dataset
        and not _is_manual_marker(source_system)
        and not _is_manual_marker(source_dataset)
    )


async def _source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    if source_type == "manual_fixture":
        return False
    if source_type == "wisdom_bit":
        return source_id.strip().upper() == "WB-TALENTO"
    if source_type == "signal":
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
        return _observed_source_row(row)
    if source_type == "decision_option":
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
        return _observed_source_row(row)
    if source_type == "backtest_case":
        exists = await conn.fetchval("SELECT to_regclass('public.backtest_runs')")
        if not exists:
            return False
        row = await conn.fetchrow(
            """
            SELECT run_mode, status, completed_at, source_system, source_dataset
              FROM backtest_runs
             WHERE workspace_id = $1 AND (id::text = $2 OR run_ref = $2)
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return _real_backtest_row(row)
    return False


def bind_selected_result(
    payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    comparison = result.get("option_comparison")
    if not isinstance(comparison, dict) or not comparison.get("options"):
        result["assumptions"] = dict(payload.get("assumptions") or {})
        result["seed"] = int(payload.get("seed") or 0)
        return result

    selected_result = comparison["options"][0]
    selected_id = str(selected_result.get("option_id") or "")
    matching = [
        option
        for option in payload.get("options") or []
        if str(option.get("option_id") or "") == selected_id
    ]
    if len(matching) != 1:
        raise ValueError("selected option provenance is ambiguous")
    selected_option = matching[0]
    selected_payload = {
        **payload,
        "seed": selected_result["seed"],
        "input_variables": (
            selected_option["input_variables"]
            if "input_variables" in selected_option
            else payload.get("input_variables")
        ),
        "assumptions": selected_option["assumptions"],
        "options": None,
    }
    exact = monte_carlo.run_single_simulation(selected_payload)
    selected_result["normalized_input_variables"] = exact["normalized_input_variables"]
    selected_result["assumptions"] = dict(selected_payload["assumptions"])
    comparison["selected_option_id"] = selected_id
    result["distribution_summary"] = exact["distribution_summary"]
    result["sensitivity"] = exact["sensitivity"][:10]
    result["normalized_input_variables"] = exact["normalized_input_variables"]
    result["assumptions"] = dict(selected_payload["assumptions"])
    result["seed"] = int(selected_payload["seed"])
    result["reproducibility_hash"] = monte_carlo.reproducibility_hash(
        {
            "model_version": result["model_version"],
            "payload": payload,
            "result": {
                "distribution_summary": result["distribution_summary"],
                "sensitivity": result["sensitivity"],
                "option_comparison": comparison,
            },
        }
    )
    return result

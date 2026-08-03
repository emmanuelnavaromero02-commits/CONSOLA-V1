from __future__ import annotations

import os
from typing import Any

from app.services.intelligence import monte_carlo
from app.services.intelligence.source_provenance import source_is_trusted


LOCAL_ENVIRONMENTS = {"test", "local", "development"}


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


async def _source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    return await source_is_trusted(
        conn, workspace_id, source_type, source_id, allow_manual=False
    )


def bind_selected_result(
    payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    comparison = result.get("option_comparison")
    if not isinstance(comparison, dict) or not comparison.get("options"):
        result["assumptions"] = dict(payload.get("assumptions") or {})
        result["seed"] = int(payload.get("seed") or 0)
        return result

    if comparison.get("status") == "ambiguous":
        comparison["selected_option_id"] = None
        result["assumptions"] = {
            **dict(payload.get("assumptions") or {}),
            "model_default_values": monte_carlo.monte_carlo_contract.default_assumptions(
                str(payload.get("output_metric") or "net_value"),
                set(result["normalized_input_variables"]),
            ),
        }
        result["seed"] = int(payload.get("seed") or 0)
        return result

    selected_id = str(comparison.get("selected_option_id") or "")
    selected_result = next(
        (
            option
            for option in comparison["options"]
            if str(option.get("option_id") or "") == selected_id
        ),
        None,
    )
    if selected_result is None:
        raise ValueError("selected option result is missing")
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
    selected_result["assumptions"] = {
        **dict(selected_payload["assumptions"]),
        "model_default_values": monte_carlo.monte_carlo_contract.default_assumptions(
            str(selected_payload.get("output_metric") or "net_value"),
            set(exact["normalized_input_variables"]),
        ),
    }
    result["distribution_summary"] = exact["distribution_summary"]
    result["sensitivity"] = exact["sensitivity"][:10]
    result["normalized_input_variables"] = exact["normalized_input_variables"]
    result["assumptions"] = dict(selected_result["assumptions"])
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

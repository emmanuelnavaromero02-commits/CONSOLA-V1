from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException

from app.services import mcp_registry
from app.services.intelligence import (
    calibration_service,
    decision_orchestrator,
    monte_carlo_service,
)
from app.services.intelligence.gold_fetcher import query_intelligence_dataset_rows


SOURCE_DATASET = "sap_successfactors_talent_simulation_inputs"
SOURCE_ID = "WB-TALENTO"
MARKET_PROVIDER = "banxico"
MARKET_METRIC = "usd_mxn_fix"
MODEL_VERSION = "sf_market_validation.v1"
CALIBRATION_GROUP = "sap_successfactors:talent_readiness"
CALIBRATION_MODEL = "bayesian_calibration.v1"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _require_cartridge(user: dict, cartridge_id: str) -> None:
    allowed = user.get("allowed_cartridges")
    if not isinstance(allowed, list):
        environment = os.environ.get("APP_ENV", "production").strip().lower()
        if environment in {"production", "prod"}:
            raise HTTPException(403, "workspace cartridge scope is required")
        return
    if "*" not in allowed and cartridge_id not in allowed:
        raise HTTPException(403, f"cartridge not active for workspace: {cartridge_id}")


async def _source_snapshot(user: dict) -> dict[str, Any] | None:
    _require_cartridge(user, "sap_successfactors")
    rows = await query_intelligence_dataset_rows(SOURCE_DATASET, user, limit=20)
    return next((row for row in rows if str(row.get("source_id")) == SOURCE_ID), None)


async def _optional_source_snapshot(user: dict) -> dict[str, Any] | None:
    try:
        return await _source_snapshot(user)
    except HTTPException as exc:
        if exc.status_code not in {404, 503}:
            raise
        return None


async def _market_snapshot(user: dict) -> dict[str, Any]:
    result = await mcp_registry.invoke(
        "mcp-infra",
        "market_context_read",
        {
            "provider": MARKET_PROVIDER,
            "metric_names": [MARKET_METRIC],
            "usable_only": True,
            "limit": 10,
        },
        user=user,
    )
    rows = result.get("context") if isinstance(result, dict) else None
    row = next(
        (
            item
            for item in (rows or [])
            if isinstance(item, dict)
            and item.get("metric_name") == MARKET_METRIC
            and item.get("usable") is True
        ),
        None,
    )
    if not row:
        raise HTTPException(422, f"usable market context unavailable: {MARKET_METRIC}")
    return row


def _simulation_payload(source: dict[str, Any]) -> dict[str, Any]:
    variables = _json_object(source.get("input_variables_json"))
    if not variables:
        raise HTTPException(422, "SuccessFactors simulation inputs are unavailable")
    variables["cost_per_day"] = {
        "type": "external_market_context",
        "metric_name": MARKET_METRIC,
        "uncertainty_pct": "0.02",
    }
    assumptions = _json_object(source.get("assumptions_json"))
    assumptions["market_validation"] = {
        "contract_version": MODEL_VERSION,
        "scenario": "normalized_fx_delay_exposure",
        "normalized_exposure": "one_usd_equivalent_per_delay_day",
        "financial_forecast": False,
        "causal_claim": False,
        "recommendation_only": True,
        "source_mode": str(source.get("source_mode") or ""),
    }
    return {
        "source_type": "wisdom_bit",
        "source_id": SOURCE_ID,
        "horizon_days": 30,
        "iterations": 2000,
        "seed": 45121,
        "model_version": MODEL_VERSION,
        "input_variables": variables,
        "assumptions": assumptions,
        "use_external_market_context": True,
        "output_metric": "cost",
        "evidence_refs": _json_list(source.get("evidence_refs_json")),
    }


def _market_assumption(simulation: dict[str, Any]) -> dict[str, Any]:
    assumptions = _json_object(simulation.get("assumptions"))
    items = assumptions.get("external_market_context")
    if not isinstance(items, list):
        return {}
    return next(
        (
            item
            for item in items
            if isinstance(item, dict) and item.get("metric_name") == MARKET_METRIC
        ),
        {},
    )


def _evidence_refs(simulation: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"type": str(item["type"]), "id": str(item["id"])}
        for item in _json_list(simulation.get("evidence_refs"))
        if isinstance(item, dict) and item.get("type") and item.get("id")
    ]


async def _bayes_states(user: dict) -> list[dict[str, Any]]:
    result = await calibration_service.get_state(
        user,
        calibration_group=CALIBRATION_GROUP,
        model_version=CALIBRATION_MODEL,
        limit=10,
    )
    return result.get("states") if isinstance(result.get("states"), list) else []


def _report(
    source: dict[str, Any] | None,
    simulation: dict[str, Any] | None,
    orchestration: dict[str, Any] | None,
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    market = _market_assumption(simulation or {})
    summary = _json_object((simulation or {}).get("distribution_summary"))
    source_mode = str((source or {}).get("source_mode") or "")
    complete = bool(simulation and orchestration and market)
    status = "ready" if complete and states and source_mode == "cpa_real" else "partial" if complete else "insufficient_data"
    state = states[0] if states else {}
    return {
        "status": status,
        "source": {
            "dataset": SOURCE_DATASET,
            "source_id": SOURCE_ID,
            "input_status": str((source or {}).get("input_status") or "missing"),
            "source_mode": source_mode or "missing",
            "employee_count": int((source or {}).get("employee_count") or 0),
            "confidence": (source or {}).get("confidence"),
        },
        "market_context": {
            "provider": MARKET_PROVIDER,
            "metric_name": MARKET_METRIC,
            "as_of": market.get("as_of"),
            "unit": market.get("unit"),
            "confidence": market.get("confidence"),
            "freshness_status": market.get("freshness_status"),
            "distribution": market.get("bounds") or {},
        },
        "simulation": {
            "simulation_id": (simulation or {}).get("simulation_id"),
            "model_version": (simulation or {}).get("model_version"),
            "output_metric": (simulation or {}).get("output_metric"),
            "p10": summary.get("p10"),
            "p50": summary.get("p50"),
            "p90": summary.get("p90"),
            "updated_at": (simulation or {}).get("updated_at"),
            "market_evidence_count": sum(
                1 for item in _evidence_refs(simulation or {}) if item["type"] == "market_context"
            ),
        },
        "bayes": {
            "status": "ready" if states else "insufficient_data",
            "calibration_group": CALIBRATION_GROUP,
            "sample_count": int(state.get("sample_count") or 0),
            "evidence_policy": "evidence_only",
        },
        "orchestration": {
            "orchestration_id": (orchestration or {}).get("orchestration_id"),
            "problem_type": (orchestration or {}).get("problem_type"),
            "action_recommended": bool((orchestration or {}).get("action_recommended")),
            "external_action_id": (orchestration or {}).get("external_action_id"),
        },
        "policy": {
            "recommendation_only": True,
            "causal_claim": False,
            "financial_forecast": False,
            "creates_calibration_observation": False,
            "automatic_action": False,
            "external_writeback": False,
        },
    }


async def get_validation(user: dict) -> dict[str, Any]:
    source = await _optional_source_snapshot(user)
    simulations = await monte_carlo_service.list_simulations(
        user, source_type="wisdom_bit", source_id=SOURCE_ID, limit=50
    )
    simulation = next(
        (
            item
            for item in simulations.get("simulations", [])
            if item.get("model_version") == MODEL_VERSION
        ),
        None,
    )
    orchestration = None
    if simulation:
        runs = await decision_orchestrator.list_orchestrations(
            user,
            source_type="monte_carlo_simulation",
            source_id=str(simulation["simulation_id"]),
            limit=1,
        )
        orchestration = next(iter(runs.get("orchestrations") or []), None)
    return _report(source, simulation, orchestration, await _bayes_states(user))


async def run_validation(user: dict) -> dict[str, Any]:
    source = await _source_snapshot(user)
    if not source or str(source.get("input_status")) != "ready":
        raise HTTPException(422, "SuccessFactors validation inputs are not ready")
    market = await _market_snapshot(user)
    simulation = (await monte_carlo_service.run_simulation(user, _simulation_payload(source)))[
        "simulation"
    ]
    assumption = _market_assumption(simulation)
    if str(assumption.get("as_of")) != str(market.get("as_of")):
        raise HTTPException(409, "market context changed during validation")
    orchestration = (
        await decision_orchestrator.orchestrate(
            user,
            {
                "source_type": "monte_carlo_simulation",
                "source_id": simulation["simulation_id"],
                "title": "Validacion de contexto externo WB-TALENTO",
                "description": "Costo normalizado de demora con evidencia macro gobernada.",
                "metrics": {"output_metric": "normalized_fx_delay_cost"},
                "time_horizon": "30d",
                "constraints": {
                    "recommendation_only": True,
                    "no_external_writeback": True,
                    "no_causal_claim": True,
                    "no_pii": True,
                },
                "evidence_refs": _evidence_refs(simulation),
            },
        )
    )["orchestration"]
    if orchestration.get("action_recommended") or orchestration.get("external_action_id"):
        raise HTTPException(500, "recommendation-only validation attempted an action")
    return _report(source, simulation, orchestration, await _bayes_states(user))

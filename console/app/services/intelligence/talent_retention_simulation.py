from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.services.intelligence import monte_carlo_service
from app.services.intelligence.gold_fetcher import query_gold_dataset_population
from app.services.intelligence.monte_carlo import MAX_SEED

logger = logging.getLogger(__name__)

SIMULATION_INPUTS_DATASET = "sap_successfactors_talent_simulation_inputs"
WISDOM_BIT_ID = "WB-TALENTO"
ITERATIONS = 10_000
HORIZON_DAYS = 90
HIGH_RISK_THRESHOLD = 70.0


def _stable_seed(manifest: dict[str, Any], row: dict[str, Any]) -> int:
    basis = json.dumps(
        {
            "head_run_id": str(manifest.get("head_run_id") or ""),
            "head_generation": manifest.get("head_generation"),
            "input_variables": str(row.get("input_variables_json") or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (MAX_SEED + 1)


def _parse_json_field(row: dict[str, Any], field: str):
    raw = row.get(field)
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(str(raw))


async def run_for_workspace(user: dict) -> dict[str, Any]:
    rows, manifest = await query_gold_dataset_population(
        SIMULATION_INPUTS_DATASET, user
    )
    if not rows:
        return {"status": "waiting_for_data", "reason": "sin insumos publicados"}
    row = max(rows, key=lambda item: str(item.get("materialized_at") or ""))
    input_status = str(row.get("input_status") or "").strip().lower()
    if input_status != "ready" and input_status != "partial":
        return {
            "status": "blocked",
            "reason": str(row.get("user_status_label") or "insumos bloqueados"),
        }
    variables = _parse_json_field(row, "input_variables_json")
    if not isinstance(variables, dict) or not variables:
        return {
            "status": "blocked",
            "reason": "input_variables_json ausente o inválido",
        }
    evidence_refs = _parse_json_field(row, "evidence_refs_json") or []
    payload: dict[str, Any] = {
        "source_type": "wisdom_bit",
        "source_id": WISDOM_BIT_ID,
        "output_metric": "net_value",
        "iterations": ITERATIONS,
        "horizon_days": HORIZON_DAYS,
        "seed": _stable_seed(manifest, row),
        "input_variables": variables,
        "breach_threshold": HIGH_RISK_THRESHOLD,
        "breach_direction": "above",
        "evidence_refs": evidence_refs,
    }
    result = await monte_carlo_service.run_simulation(user, payload)
    simulation = (result or {}).get("simulation") or {}
    return {
        "status": "simulated",
        "input_status": input_status,
        "simulation_id": simulation.get("simulation_id"),
        "seed": payload["seed"],
        "iterations": ITERATIONS,
    }


async def run_best_effort(user: dict) -> dict[str, Any] | None:
    try:
        return await run_for_workspace(user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("talent retention simulation sweep failed: %s", exc)
        return None

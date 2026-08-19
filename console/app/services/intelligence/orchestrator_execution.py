from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import (
    calibration,
    monte_carlo_service,
    orchestrator_execution_truth as truth,
)
from app.services.intelligence.minimax_allocation import (
    MinimaxValidationError,
    solve_minimax_allocation,
)
from app.services.intelligence.permutation_test import (
    PermutationValidationError,
    run_permutation_test,
)
from app.services.intelligence.utils import json_default, json_dumps, public_json

FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}
TERMINAL_STATUSES = {"succeeded", "skipped", "failed", "candidate_only"}
EXECUTABLE_ENGINES = {
    "monte_carlo",
    "bayesian_calibration",
    "permutation_test",
    "minimax_allocation",
}
CANDIDATE_ENGINES = {
    "constrained_optimizer_candidate",
    "mpc_candidate",
    "game_theory_candidate",
}
CONTEXT_ONLY_ENGINES = {"decision_intelligence"}


class OrchestratorExecutionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class EngineContract:
    engine_name: str
    status: str
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    timeout_ms: int
    retries: int
    deterministic: bool
    side_effects: str
    requires_approval: bool


ENGINE_REGISTRY: dict[str, EngineContract] = {
    "monte_carlo": EngineContract(
        engine_name="monte_carlo",
        status="allowlisted",
        input_contract={"required": ["engine_inputs.monte_carlo"]},
        output_contract={"evidence": ["simulation_id", "distribution_summary"]},
        timeout_ms=15_000,
        retries=0,
        deterministic=True,
        side_effects="none",
        requires_approval=False,
    ),
    "bayesian_calibration": EngineContract(
        engine_name="bayesian_calibration",
        status="allowlisted",
        input_contract={
            "required": ["calibration_group or calibration_observation source"]
        },
        output_contract={
            "evidence": ["posterior_mean", "sample_count", "confidence_score"]
        },
        timeout_ms=5_000,
        retries=0,
        deterministic=True,
        side_effects="none",
        requires_approval=False,
    ),
    "permutation_test": EngineContract(
        engine_name="permutation_test",
        status="allowlisted",
        input_contract={"required": ["engine_inputs.permutation_test"]},
        output_contract={"evidence": ["p_value", "verdict", "input_digest"]},
        timeout_ms=5_000,
        retries=0,
        deterministic=True,
        side_effects="none",
        requires_approval=False,
    ),
    "minimax_allocation": EngineContract(
        engine_name="minimax_allocation",
        status="allowlisted",
        input_contract={"required": ["engine_inputs.minimax_allocation"]},
        output_contract={
            "evidence": ["selected", "worst_unmitigated_regret", "input_digest"]
        },
        timeout_ms=5_000,
        retries=0,
        deterministic=True,
        side_effects="none",
        requires_approval=False,
    ),
    "decision_intelligence": EngineContract(
        engine_name="decision_intelligence",
        status="context_only",
        input_contract={"required": []},
        output_contract={"evidence": ["existing_orchestration_plan"]},
        timeout_ms=0,
        retries=0,
        deterministic=True,
        side_effects="context_only",
        requires_approval=False,
    ),
}


def _actor_id(user: dict | None) -> int | None:
    raw = (user or {}).get("id")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _short_hash(value: Any) -> str:
    return _hash(value)[:24]


def _deterministic_seed(
    *, orchestration_id: str, engine_name: str, payload: dict[str, Any]
) -> int:
    digest = _hash(
        {
            "orchestration_id": orchestration_id,
            "engine_name": engine_name,
            "payload": payload,
        }
    )
    return int(digest[:12], 16)


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _serialize_execution(row: Any) -> dict[str, Any]:
    data = public_json(_row_dict(row))
    for key in ("input_summary", "result_summary"):
        data[key] = _json_obj(data.get(key))
    data["evidence_refs"] = _json_list(data.get("evidence_refs"))
    if data.get("id") is not None:
        data["id"] = int(data["id"])
    return data


def _serialize_run(row: Any) -> dict[str, Any]:
    data = public_json(_row_dict(row))
    for key in (
        "secondary_problem_types",
        "recommended_engines",
        "candidate_engines",
        "safety_notes",
        "missing_data",
    ):
        data[key] = _json_list(data.get(key))
    for key in ("engine_plan", "decision_plan"):
        data[key] = _json_obj(data.get(key))
    if data.get("external_action_id") is not None:
        data["external_action_id"] = str(data["external_action_id"])
    return data


def _validate_no_scope_fields(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            text_key = str(key)
            if text_key in FORBIDDEN_SCOPE_KEYS:
                raise OrchestratorExecutionError(
                    422, f"{path}.{text_key} is not accepted"
                )
            _validate_no_scope_fields(item, path=f"{path}.{text_key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:200]):
            _validate_no_scope_fields(item, path=f"{path}[{index}]")


def _clean_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    body = dict(payload or {})
    _validate_no_scope_fields(body)
    engine_inputs = body.get("engine_inputs") or {}
    if not isinstance(engine_inputs, dict):
        raise OrchestratorExecutionError(422, "engine_inputs must be an object")
    truth.reject_client_bayesian_version(engine_inputs, OrchestratorExecutionError)
    if any(key in engine_inputs for key in ("orchestrator", "decision_orchestrator")):
        raise OrchestratorExecutionError(422, "recursive orchestration is not accepted")
    body["engine_inputs"] = engine_inputs
    return body


def _engine_names_from_descriptors(items: Any) -> list[str]:
    output: list[str] = []
    for item in _json_list(items):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("engine") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            output.append(name)
    return output


def _executable_engines_for_problem(problem_type: str) -> list[str]:
    if problem_type in {"risk_forecast", "temporal_control"}:
        return ["monte_carlo", "bayesian_calibration"]
    if problem_type in {"resource_allocation", "budget_optimization"}:
        # E4: el hueco 'constrained_optimizer_candidate — not yet implemented'
        # tiene ya su forma acotada y EXACTA: minimax top-K de regret. El
        # candidato general sigue declarado para lo que el minimax no cubre.
        return ["monte_carlo", "minimax_allocation"]
    if problem_type == "data_quality":
        # E4: concentracion/sesgo — ¿azar o patron? — con test de permutacion.
        return ["permutation_test"]
    return []


def _candidate_engines(run: dict[str, Any]) -> list[str]:
    names = _engine_names_from_descriptors(run.get("candidate_engines"))
    return [name for name in names if name in CANDIDATE_ENGINES]


async def _load_run(
    conn: Any, *, workspace_id: str, orchestration_id: str
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT *
          FROM decision_orchestration_runs
         WHERE workspace_id = $1
           AND orchestration_id = $2
         LIMIT 1
        """,
        workspace_id,
        orchestration_id,
    )
    if not row:
        raise OrchestratorExecutionError(404, "decision orchestration not found")
    return _serialize_run(row)


async def _existing_execution(
    conn: Any,
    *,
    workspace_id: str,
    orchestration_id: str,
    engine_name: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT *
          FROM decision_orchestration_executions
         WHERE workspace_id = $1
           AND orchestration_id = $2
           AND engine_name = $3
           AND execution_status = ANY($4::text[])
         ORDER BY created_at ASC, id ASC
         LIMIT 1
        """,
        workspace_id,
        orchestration_id,
        engine_name,
        sorted(TERMINAL_STATUSES),
    )
    return _serialize_execution(row) if row else None


async def _insert_execution(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    orchestration_id: str,
    engine_name: str,
    execution_status: str,
    input_summary: dict[str, Any],
    result_summary: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    created_by: int | None,
    error_code: str | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> dict[str, Any]:
    input_hash = _short_hash(
        {
            "orchestration_id": orchestration_id,
            "engine_name": engine_name,
            "input_summary": input_summary,
        }
    )
    output_hash = _short_hash(
        {
            "execution_status": execution_status,
            "result_summary": result_summary,
            "evidence_refs": evidence_refs,
            "error_code": error_code,
            "error_message": error_message,
        }
    )
    now = datetime.now(timezone.utc)
    row = await conn.fetchrow(
        """
        INSERT INTO decision_orchestration_executions (
            tenant_id, workspace_id, orchestration_id, engine_name,
            execution_status, input_hash, output_hash, input_summary,
            result_summary, evidence_refs, error_code, error_message,
            started_at, finished_at, created_by
        )
        VALUES (
            $1, $2, $3, $4,
            $5, $6, $7, $8::jsonb,
            $9::jsonb, $10::jsonb, $11, $12,
            $13, $14, $15
        )
        ON CONFLICT (workspace_id, orchestration_id, engine_name, input_hash)
        DO UPDATE SET
            execution_status = decision_orchestration_executions.execution_status,
            updated_at = decision_orchestration_executions.updated_at
        RETURNING *
        """,
        tenant_id,
        workspace_id,
        orchestration_id,
        engine_name,
        execution_status,
        input_hash,
        output_hash,
        json_dumps(input_summary),
        json_dumps(result_summary),
        json_dumps(evidence_refs),
        error_code,
        error_message[:1000] if error_message else None,
        started_at or now,
        finished_at or now,
        created_by,
    )
    return _serialize_execution(row)


def _manual_fixture_disabled(source_type: str) -> bool:
    if source_type != "manual_fixture":
        return False
    app_env = os.environ.get("APP_ENV")
    return app_env is None or app_env.strip().lower() not in {
        "test",
        "local",
        "development",
    }


def _monte_carlo_payload(
    *,
    run: dict[str, Any],
    engine_inputs: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    raw = engine_inputs.get("monte_carlo")
    if raw is None:
        return None, "missing_monte_carlo_inputs"
    if not isinstance(raw, dict):
        raise OrchestratorExecutionError(
            422, "engine_inputs.monte_carlo must be an object"
        )
    payload = dict(raw)
    source_type = str(payload.get("source_type") or "").strip()
    if not source_type:
        return None, "missing_monte_carlo_source_type"
    if _manual_fixture_disabled(source_type):
        return None, "manual_fixture_disabled"
    if "seed" not in payload:
        payload["seed"] = _deterministic_seed(
            orchestration_id=str(run["orchestration_id"]),
            engine_name="monte_carlo",
            payload=payload,
        )
    evidence_refs = payload.get("evidence_refs") or []
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    evidence_refs = [
        {"type": str(item.get("type")), "id": str(item.get("id"))}
        for item in evidence_refs
        if isinstance(item, dict) and item.get("type") and item.get("id")
    ][:19]
    evidence_refs.append(
        {"type": "decision_orchestration_run", "id": str(run["orchestration_id"])}
    )
    payload["evidence_refs"] = evidence_refs
    return payload, None


async def _run_monte_carlo(
    user: dict,
    *,
    run: dict[str, Any],
    engine_inputs: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None, str | None]:
    payload, skipped_reason = _monte_carlo_payload(run=run, engine_inputs=engine_inputs)
    if skipped_reason:
        return (
            "skipped",
            {"status": "skipped", "reason": skipped_reason},
            [],
            skipped_reason,
            None,
        )
    assert payload is not None
    try:
        response = await asyncio.wait_for(
            monte_carlo_service.run_simulation(user, payload),
            timeout=ENGINE_REGISTRY["monte_carlo"].timeout_ms / 1000,
        )
    except HTTPException as exc:
        code = f"monte_carlo_http_{exc.status_code}"
        if exc.status_code < 500:
            return (
                "skipped",
                {"status": "skipped", "reason": code, "detail": str(exc.detail)},
                [],
                code,
                str(exc.detail),
            )
        return (
            "failed",
            {"status": "failed", "reason": code},
            [],
            code,
            str(exc.detail),
        )
    except TimeoutError as exc:
        return (
            "failed",
            {"status": "failed", "reason": "monte_carlo_timeout"},
            [],
            "monte_carlo_timeout",
            str(exc),
        )
    except Exception as exc:
        return (
            "failed",
            {"status": "failed", "reason": "monte_carlo_error"},
            [],
            "monte_carlo_error",
            str(exc),
        )
    simulation = response.get("simulation") if isinstance(response, dict) else {}
    if not isinstance(simulation, dict) or not simulation.get("simulation_id"):
        return (
            "failed",
            {"status": "failed", "reason": "monte_carlo_empty_result"},
            [],
            "monte_carlo_empty_result",
            None,
        )
    summary = {
        "status": "succeeded",
        "simulation_id": simulation.get("simulation_id"),
        "source_type": simulation.get("source_type"),
        "source_id": simulation.get("source_id"),
        "distribution_summary": simulation.get("distribution_summary") or {},
        "reproducibility_hash": simulation.get("reproducibility_hash"),
    }
    evidence_refs = [
        {"type": "monte_carlo_simulation", "id": str(simulation["simulation_id"])}
    ]
    return "succeeded", summary, evidence_refs, None, None


async def _bayes_state_from_source(
    conn: Any,
    *,
    workspace_id: str,
    run: dict[str, Any],
) -> tuple[str | None, str | None]:
    if run.get("source_type") != "calibration_observation":
        return None, None
    row = await conn.fetchrow(
        """
        SELECT calibration_group, model_version
          FROM calibration_observations
         WHERE workspace_id = $1
           AND (observation_id = $2 OR id::text = $2)
           AND provenance_status = 'verified'
           AND provenance_reason = 'durable_binary_evaluation'
           AND authoritative_calibration_group = calibration_group
         LIMIT 1
        """,
        workspace_id,
        str(run.get("source_id") or ""),
    )
    if not row:
        return None, None
    data = _row_dict(row)
    return (
        str(data.get("calibration_group") or "") or None,
        str(data.get("model_version") or "") or None,
    )


async def _run_bayesian_lookup(
    conn: Any,
    *,
    workspace_id: str,
    run: dict[str, Any],
    engine_inputs: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None, str | None]:
    raw = engine_inputs.get("bayesian_calibration") or {}
    if raw is not None and not isinstance(raw, dict):
        raise OrchestratorExecutionError(
            422, "engine_inputs.bayesian_calibration must be an object"
        )
    source_group, _source_version = await _bayes_state_from_source(
        conn,
        workspace_id=workspace_id,
        run=run,
    )
    if run.get("source_type") == "calibration_observation" and not source_group:
        reason = "untrusted_calibration_observation"
        return "skipped", {"status": "skipped", "reason": reason}, [], reason, None
    truth.reject_client_bayesian_version(engine_inputs, OrchestratorExecutionError)
    requested_group = str(raw.get("calibration_group") or "").strip()
    if source_group and requested_group and requested_group != source_group:
        reason = "calibration_group_mismatch"
        return "skipped", {"status": "skipped", "reason": reason}, [], reason, None
    group = str(source_group or requested_group).strip()
    model_version = calibration.MODEL_VERSION
    if not group:
        return (
            "skipped",
            {"status": "skipped", "reason": "missing_calibration_group"},
            [],
            "missing_calibration_group",
            None,
        )
    row = await conn.fetchrow(
        """
        SELECT *
          FROM calibration_states
         WHERE workspace_id = $1
           AND calibration_group = $2
           AND model_version = $3
           AND metrics->>'complete' = 'true'
           AND metrics->>'provenance_complete' = 'true'
           AND metrics->>'binary_evaluation_complete' = 'true'
           AND COALESCE((metrics->>'skipped_total')::integer, -1) = 0
           AND COALESCE((metrics->>'processed_total')::integer, 0) > 0
           AND (metrics->>'processed_total')::integer =
               (metrics->>'eligible_total')::integer
           AND (metrics->>'processed_total')::integer = sample_count
           AND EXISTS (
               SELECT 1
                 FROM calibration_observations observation
                WHERE observation.workspace_id = calibration_states.workspace_id
                  AND observation.calibration_group =
                      calibration_states.calibration_group
                  AND observation.model_version = calibration_states.model_version
                  AND observation.provenance_status = 'verified'
                  AND observation.provenance_reason =
                      'durable_binary_evaluation'
                  AND observation.authoritative_calibration_group =
                      observation.calibration_group
           )
         LIMIT 1
        """,
        workspace_id,
        group,
        model_version,
    )
    if not row:
        return (
            "skipped",
            {
                "status": "skipped",
                "reason": "missing_calibration_state",
                "calibration_group": group,
                "model_version": model_version,
            },
            [],
            "missing_calibration_state",
            None,
        )
    state = _row_dict(row)
    posterior = _json_obj(state.get("posterior"))
    metrics = _json_obj(state.get("metrics"))
    if incomplete := truth.incomplete_calibration_result(metrics):
        return incomplete
    sample_count = int(state.get("sample_count") or metrics.get("sample_count") or 0)
    confidence_score = state.get("confidence_score")
    if confidence_score is None:
        confidence_score = metrics.get("confidence_score") or 0.0
    summary = {
        "status": "succeeded",
        "calibration_group": group,
        "model_version": model_version,
        "posterior_mean": posterior.get("mean"),
        "posterior_alpha": posterior.get("alpha"),
        "posterior_beta": posterior.get("beta"),
        "sample_count": sample_count,
        "confidence_score": float(confidence_score or 0.0),
    }
    evidence_refs = [
        {
            "type": "calibration_state",
            "id": f"{group}:{model_version}",
        }
    ]
    return "succeeded", summary, evidence_refs, None, None


def _run_permutation(
    *, engine_inputs: dict[str, Any]
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None, str | None]:
    """E4 — motor puro y determinista; sin inputs no corre (jamas inventa)."""
    payload = engine_inputs.get("permutation_test")
    if not isinstance(payload, dict) or not payload:
        return (
            "skipped",
            {"status": "skipped", "reason": "engine_input_missing"},
            [],
            "engine_input_missing",
            None,
        )
    try:
        summary = run_permutation_test(
            categories=payload.get("categories") or [],
            draws=payload.get("draws"),
            focus_key=str(payload.get("focus_key") or ""),
            observed=payload.get("observed"),
            iterations=payload.get("iterations"),
            seed=payload.get("seed", 0),
        )
    except PermutationValidationError as exc:
        return (
            "skipped",
            {"status": "skipped", "reason": "invalid_engine_input", "detail": str(exc)},
            [],
            "invalid_engine_input",
            str(exc),
        )
    evidence_refs = [
        {
            "type": "deterministic_engine",
            "id": f"permutation:{summary['input_digest'][:16]}",
        }
    ]
    return "succeeded", summary, evidence_refs, None, None


def _run_minimax(
    *, engine_inputs: dict[str, Any]
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None, str | None]:
    """E4 — asignacion minimax exacta; sin inputs no corre (jamas inventa)."""
    payload = engine_inputs.get("minimax_allocation")
    if not isinstance(payload, dict) or not payload:
        return (
            "skipped",
            {"status": "skipped", "reason": "engine_input_missing"},
            [],
            "engine_input_missing",
            None,
        )
    try:
        summary = solve_minimax_allocation(
            candidates=payload.get("candidates") or [],
            capacity=payload.get("capacity"),
        )
    except MinimaxValidationError as exc:
        return (
            "skipped",
            {"status": "skipped", "reason": "invalid_engine_input", "detail": str(exc)},
            [],
            "invalid_engine_input",
            str(exc),
        )
    evidence_refs = [
        {
            "type": "deterministic_engine",
            "id": f"minimax:{summary['input_digest'][:16]}",
        }
    ]
    return "succeeded", summary, evidence_refs, None, None


async def _execute_engine(
    user: dict,
    *,
    conn: Any,
    workspace_id: str,
    run: dict[str, Any],
    engine_name: str,
    engine_inputs: dict[str, Any],
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str | None, str | None]:
    contract = ENGINE_REGISTRY.get(engine_name)
    if contract is None or engine_name not in EXECUTABLE_ENGINES:
        return (
            "skipped",
            {"status": "skipped", "reason": "engine_not_allowlisted"},
            [],
            "engine_not_allowlisted",
            None,
        )
    if contract.side_effects != "none":
        return (
            "skipped",
            {"status": "skipped", "reason": "engine_has_side_effects"},
            [],
            "engine_has_side_effects",
            None,
        )
    if contract.requires_approval:
        return (
            "skipped",
            {"status": "skipped", "reason": "engine_requires_approval"},
            [],
            "engine_requires_approval",
            None,
        )
    if engine_name == "monte_carlo":
        return await _run_monte_carlo(user, run=run, engine_inputs=engine_inputs)
    if engine_name == "permutation_test":
        return _run_permutation(engine_inputs=engine_inputs)
    if engine_name == "minimax_allocation":
        return _run_minimax(engine_inputs=engine_inputs)
    if engine_name == "bayesian_calibration":
        return await _run_bayesian_lookup(
            conn,
            workspace_id=workspace_id,
            run=run,
            engine_inputs=engine_inputs,
        )
    return (
        "skipped",
        {"status": "skipped", "reason": "engine_not_implemented"},
        [],
        "engine_not_implemented",
        None,
    )


async def _store_candidate_only(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    run: dict[str, Any],
    engine_name: str,
    created_by: int | None,
) -> dict[str, Any]:
    existing = await _existing_execution(
        conn,
        workspace_id=workspace_id,
        orchestration_id=str(run["orchestration_id"]),
        engine_name=engine_name,
    )
    if existing:
        return existing
    return await _insert_execution(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        orchestration_id=str(run["orchestration_id"]),
        engine_name=engine_name,
        execution_status="candidate_only",
        input_summary={
            "engine_name": engine_name,
            "problem_type": run.get("problem_type"),
            "candidate_only": True,
        },
        result_summary={
            "status": "candidate_only",
            "reason": "engine_not_implemented",
        },
        evidence_refs=[],
        created_by=created_by,
        error_code="candidate_only",
        error_message="Candidate engine is not implemented and was not executed.",
    )


async def _store_engine_result(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    run: dict[str, Any],
    engine_name: str,
    engine_inputs: dict[str, Any],
    created_by: int | None,
    status: str,
    result_summary: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    error_code: str | None,
    error_message: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    return await _insert_execution(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        orchestration_id=str(run["orchestration_id"]),
        engine_name=engine_name,
        execution_status=status,
        input_summary={
            "engine_name": engine_name,
            "problem_type": run.get("problem_type"),
            "engine_input": engine_inputs.get(engine_name) or {},
            "execution_budget": "one_per_engine_per_orchestration",
        },
        result_summary=result_summary,
        evidence_refs=evidence_refs,
        created_by=created_by,
        error_code=error_code,
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
    )


def _aggregate(run: dict[str, Any], executions: list[dict[str, Any]]) -> dict[str, Any]:
    executed = [
        item["engine_name"]
        for item in executions
        if item.get("execution_status") == "succeeded"
    ]
    skipped = [
        {
            "engine": item.get("engine_name"),
            "status": "skipped",
            "reason": item.get("error_code")
            or item.get("result_summary", {}).get("reason"),
        }
        for item in executions
        if item.get("execution_status") == "skipped"
    ]
    failed = [
        {
            "engine": item.get("engine_name"),
            "status": "failed",
            "reason": item.get("error_code")
            or item.get("result_summary", {}).get("reason"),
        }
        for item in executions
        if item.get("execution_status") == "failed"
    ]
    candidates = [
        {"engine": item.get("engine_name"), "status": "candidate_only"}
        for item in executions
        if item.get("execution_status") == "candidate_only"
    ]
    if run.get("problem_type") == "insufficient_data" and not skipped:
        skipped.append(
            {
                "engine": "internal_engines",
                "status": "skipped",
                "reason": "insufficient_data",
            }
        )
    evidence_refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in executions:
        for ref in item.get("evidence_refs") or []:
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("type") or ""), str(ref.get("id") or ""))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            evidence_refs.append({"type": key[0], "id": key[1]})
    if run.get("external_action_id"):
        recommended_next_step = "review_sandbox_action_proposal"
    elif failed:
        recommended_next_step = "review_engine_failures"
    elif executed:
        recommended_next_step = "review_engine_evidence"
    else:
        recommended_next_step = "provide_required_engine_inputs"
    return {
        "orchestration_id": run.get("orchestration_id"),
        "problem_type": run.get("problem_type"),
        "executed_engines": executed,
        "context_engines": sorted(CONTEXT_ONLY_ENGINES),
        "candidate_engines": candidates,
        "skipped_engines": skipped,
        "failed_engines": failed,
        "confidence": run.get("confidence"),
        "evidence_refs": evidence_refs,
        "recommended_next_step": recommended_next_step,
    }


async def _update_engine_plan(
    conn: Any,
    workspace_id: str,
    run: dict[str, Any],
    aggregate: dict[str, Any],
) -> None:
    engine_plan = _json_obj(run.get("engine_plan"))
    engine_plan.update(
        {
            "mode": "internal_engine_execution",
            "available_engines_executed": bool(aggregate["executed_engines"]),
            "candidate_engines_executed": False,
            "execution_budget": "one_per_engine_per_orchestration",
            "last_execution_summary": {
                "executed_engines": aggregate["executed_engines"],
                "skipped_engines": aggregate["skipped_engines"],
                "failed_engines": aggregate["failed_engines"],
                "candidate_engines": aggregate["candidate_engines"],
            },
        }
    )
    await conn.execute(
        """
        UPDATE decision_orchestration_runs
           SET engine_plan = $3::jsonb,
               updated_at = NOW()
         WHERE workspace_id = $1
           AND orchestration_id = $2
        """,
        workspace_id,
        str(run["orchestration_id"]),
        json_dumps(engine_plan),
    )


async def execute_engines(
    user: dict,
    orchestration_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = _clean_payload(payload)
    engine_inputs = body["engine_inputs"]
    created_by = _actor_id(user)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        run = await _load_run(
            conn, workspace_id=workspace_id, orchestration_id=orchestration_id
        )
        if not await truth.execution_source_trusted(
            conn, workspace_id, run, not _manual_fixture_disabled("manual_fixture")
        ):
            raise OrchestratorExecutionError(409, "source_provenance_untrusted")
        executions: list[dict[str, Any]] = []
        for engine_name in _candidate_engines(run):
            executions.append(
                await _store_candidate_only(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    run=run,
                    engine_name=engine_name,
                    created_by=created_by,
                )
            )
        if run.get("problem_type") != "insufficient_data":
            for engine_name in _executable_engines_for_problem(
                str(run.get("problem_type"))
            ):
                existing = await _existing_execution(
                    conn,
                    workspace_id=workspace_id,
                    orchestration_id=str(run["orchestration_id"]),
                    engine_name=engine_name,
                )
                if existing:
                    executions.append(existing)
                    continue
                started_at = datetime.now(timezone.utc)
                try:
                    (
                        status,
                        summary,
                        evidence_refs,
                        error_code,
                        error_message,
                    ) = await _execute_engine(
                        user,
                        conn=conn,
                        workspace_id=workspace_id,
                        run=run,
                        engine_name=engine_name,
                        engine_inputs=engine_inputs,
                    )
                except Exception as exc:
                    status = "failed"
                    summary = {"status": "failed", "reason": "engine_error"}
                    evidence_refs = []
                    error_code = "engine_error"
                    error_message = str(exc)
                finished_at = datetime.now(timezone.utc)
                executions.append(
                    await _store_engine_result(
                        conn,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        run=run,
                        engine_name=engine_name,
                        engine_inputs=engine_inputs,
                        created_by=created_by,
                        status=status,
                        result_summary=summary,
                        evidence_refs=evidence_refs,
                        error_code=error_code,
                        error_message=error_message,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                )
        aggregate = _aggregate(run, executions)
        await _update_engine_plan(conn, workspace_id, run, aggregate)
    return {"aggregate": aggregate, "executions": executions}


async def list_executions(user: dict, orchestration_id: str) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        run = await _load_run(
            conn, workspace_id=workspace_id, orchestration_id=orchestration_id
        )
        rows = await conn.fetch(
            """
            SELECT *
              FROM decision_orchestration_executions
             WHERE workspace_id = $1
               AND orchestration_id = $2
             ORDER BY created_at ASC, id ASC
            """,
            workspace_id,
            orchestration_id,
        )
        executions = [_serialize_execution(row) for row in rows]
        aggregate = _aggregate(run, executions)
    return {"aggregate": aggregate, "executions": executions}

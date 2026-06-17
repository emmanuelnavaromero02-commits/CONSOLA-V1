from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services import auth, external_actions
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence.utils import json_dumps, public_json, sample_hash


MODEL_VERSION = "decision_orchestrator.v1"
SOURCE_TYPES = {
    "control_room_item",
    "agent_alert",
    "intelligence_signal",
    "monte_carlo_simulation",
    "calibration_observation",
    "manual_fixture",
}
PROBLEM_TYPES = {
    "risk_forecast",
    "resource_allocation",
    "budget_optimization",
    "capacity_planning",
    "scheduling",
    "temporal_control",
    "multi_actor_strategy",
    "simple_action",
    "data_quality",
    "insufficient_data",
    "unknown",
}
FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}
AVAILABLE_ENGINES = {
    "decision_intelligence",
    "monte_carlo",
    "bayesian_calibration",
    "agent_monitor",
    "external_action_framework",
}
CANDIDATE_ENGINES = {
    "constrained_optimizer_candidate",
    "mpc_candidate",
    "game_theory_candidate",
}


class DecisionOrchestratorError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class EngineRoute:
    recommended: tuple[str, ...]
    candidates: tuple[str, ...]
    notes: tuple[str, ...]


ENGINE_ROUTES: dict[str, EngineRoute] = {
    "risk_forecast": EngineRoute(
        ("decision_intelligence", "monte_carlo", "bayesian_calibration"),
        (),
        ("Use existing forecast evidence; Monte Carlo and Bayes are recommended but not executed in 19A.",),
    ),
    "resource_allocation": EngineRoute(
        ("decision_intelligence", "monte_carlo"),
        ("constrained_optimizer_candidate",),
        ("Requires constrained optimizer for allocation decisions; candidate is advisory only.",),
    ),
    "budget_optimization": EngineRoute(
        ("decision_intelligence", "monte_carlo"),
        ("constrained_optimizer_candidate",),
        ("Requires constrained optimizer for budget trade-offs; candidate is advisory only.",),
    ),
    "capacity_planning": EngineRoute(
        ("decision_intelligence", "monte_carlo"),
        ("constrained_optimizer_candidate",),
        ("Use simulation for risk ranges; optimizer candidate is not implemented.",),
    ),
    "scheduling": EngineRoute(
        ("decision_intelligence",),
        ("constrained_optimizer_candidate",),
        ("Scheduling needs an optimizer candidate; no scheduling engine is executed.",),
    ),
    "temporal_control": EngineRoute(
        ("decision_intelligence", "monte_carlo", "bayesian_calibration"),
        ("mpc_candidate",),
        ("MPC is candidate-only; 19A records the plan without control execution.",),
    ),
    "multi_actor_strategy": EngineRoute(
        ("decision_intelligence",),
        ("game_theory_candidate",),
        ("Game-theory reasoning is candidate-only and not executed.",),
    ),
    "simple_action": EngineRoute(
        ("decision_intelligence", "external_action_framework"),
        (),
        ("External action framework can propose sandbox actions requiring human approval.",),
    ),
    "data_quality": EngineRoute(
        ("decision_intelligence", "agent_monitor"),
        (),
        ("Use Control Room and Agent Monitor evidence; no external action is executed.",),
    ),
    "insufficient_data": EngineRoute(
        ("decision_intelligence",),
        (),
        ("Collect missing evidence before running engines or proposing actions.",),
    ),
    "unknown": EngineRoute(
        ("decision_intelligence",),
        (),
        ("No confident route; keep as advisory plan only.",),
    ),
}

KEYWORDS: dict[str, tuple[str, ...]] = {
    "data_quality": (
        "data quality",
        "freshness",
        "stale",
        "schema",
        "null",
        "duplicate",
        "missing field",
        "pipeline",
        "lineage",
    ),
    "multi_actor_strategy": (
        "competitor",
        "vendor",
        "negotiation",
        "market",
        "actor",
        "strategic",
        "game theory",
    ),
    "temporal_control": (
        "feedback",
        "real-time",
        "trajectory",
        "control",
        "dynamic",
        "time horizon",
        "lead time",
    ),
    "scheduling": ("schedule", "scheduling", "shift", "calendar", "roster", "slot"),
    "budget_optimization": (
        "budget",
        "cost",
        "spend",
        "margin",
        "opex",
        "capex",
        "savings",
    ),
    "resource_allocation": (
        "resource",
        "allocation",
        "allocate",
        "staffing",
        "headcount",
        "assignment",
    ),
    "capacity_planning": (
        "capacity",
        "demand",
        "utilization",
        "backlog",
        "throughput",
        "forecast demand",
    ),
    "risk_forecast": (
        "risk",
        "forecast",
        "probability",
        "breach",
        "anomaly",
        "deviation",
        "variance",
        "uncertainty",
        "monte carlo",
    ),
    "simple_action": (
        "notify",
        "ticket",
        "escalate",
        "send",
        "create task",
        "follow up",
        "remediate",
    ),
}


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _manual_fixture_allowed() -> bool:
    app_env = os.environ.get("APP_ENV", "production").strip().lower()
    if app_env in {"development", "dev", "test", "testing"}:
        return True
    return _truthy(os.environ.get("DECISION_ORCHESTRATOR_ALLOW_MANUAL_FIXTURE"))


def action_creation_enabled() -> bool:
    return _truthy(os.environ.get("DECISION_ORCHESTRATOR_CREATE_ACTIONS"), default=False)


def _actor_id(user: dict | None) -> int | None:
    raw = (user or {}).get("id")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_text(item) for item in value[:50])
    return str(value)


def _validate_no_scope_fields(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_SCOPE_KEYS:
                raise DecisionOrchestratorError(422, f"{path}.{key_text} is not accepted")
            _validate_no_scope_fields(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:200]):
            _validate_no_scope_fields(item, path=f"{path}[{index}]")


def _source_summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": source.get("title")
        or source.get("summary")
        or source.get("source_id")
        or source.get("signal_id")
        or source.get("simulation_id")
        or source.get("observation_id"),
        "description": source.get("description")
        or source.get("reasoning_summary")
        or source.get("summary")
        or _text(source.get("metadata"))[:1200],
        "metadata": _json_obj(source.get("metadata")),
    }


def normalize_signal(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    source_data = _source_summary(source)
    metadata = source_data["metadata"]
    evidence_refs = payload.get("evidence_refs") or metadata.get("evidence_refs") or []
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    return {
        "source_type": payload.get("source_type"),
        "source_id": payload.get("source_id"),
        "title": payload.get("title") or source_data.get("title") or "",
        "description": payload.get("description") or source_data.get("description") or "",
        "metrics": payload.get("metrics") or metadata.get("metrics") or {},
        "entities": payload.get("entities") or metadata.get("entities") or [],
        "time_horizon": payload.get("time_horizon") or metadata.get("time_horizon"),
        "constraints": payload.get("constraints") or metadata.get("constraints") or {},
        "evidence_refs": evidence_refs[:20],
        "source_metadata": {
            "severity": source.get("severity"),
            "signal_type": source.get("signal_type"),
            "item_kind": source.get("item_kind"),
            "status": source.get("status"),
            "domain": source.get("domain"),
            "metric": source.get("metric"),
            "dataset": source.get("dataset") or source.get("source_dataset"),
        },
    }


def _score_text(normalized: dict[str, Any]) -> tuple[str, dict[str, int]]:
    text = " ".join(
        (
            _text(normalized.get("title")),
            _text(normalized.get("description")),
            _text(normalized.get("metrics")),
            _text(normalized.get("entities")),
            _text(normalized.get("constraints")),
            _text(normalized.get("source_metadata")),
        )
    ).lower()
    scores: dict[str, int] = {}
    for problem_type, keywords in KEYWORDS.items():
        scores[problem_type] = sum(1 for keyword in keywords if keyword in text)
    source_type = str(normalized.get("source_type") or "")
    if source_type in {"monte_carlo_simulation", "calibration_observation"}:
        scores["risk_forecast"] = scores.get("risk_forecast", 0) + 2
    if source_type == "agent_alert":
        scores["data_quality"] = scores.get("data_quality", 0) + 1
    if normalized.get("time_horizon"):
        scores["risk_forecast"] = scores.get("risk_forecast", 0) + 1
    if normalized.get("constraints"):
        scores["resource_allocation"] = scores.get("resource_allocation", 0) + 1
    return text, scores


def classify_problem(normalized: dict[str, Any]) -> dict[str, Any]:
    text, scores = _score_text(normalized)
    missing_data: list[str] = []
    if not normalized.get("title"):
        missing_data.append("title")
    if not normalized.get("description"):
        missing_data.append("description")
    if not normalized.get("metrics"):
        missing_data.append("metrics")
    if not normalized.get("evidence_refs"):
        missing_data.append("evidence_refs")

    if len(text.strip()) < 12 and len(missing_data) >= 3:
        return {
            "problem_type": "insufficient_data",
            "secondary_problem_types": [],
            "confidence": 0.35,
            "missing_data": missing_data,
            "reasoning_summary": "Insufficient signal detail to classify reliably.",
        }

    priority = [
        "data_quality",
        "multi_actor_strategy",
        "temporal_control",
        "scheduling",
        "budget_optimization",
        "resource_allocation",
        "capacity_planning",
        "risk_forecast",
        "simple_action",
    ]
    ranked = sorted(
        ((problem, scores.get(problem, 0)) for problem in priority),
        key=lambda item: (-item[1], priority.index(item[0])),
    )
    best, score = ranked[0]
    if score <= 0:
        best = "unknown"
    secondaries = [
        problem
        for problem, value in ranked[1:]
        if value > 0 and problem != best
    ][:3]
    confidence = round(min(0.92, max(0.42, 0.48 + score * 0.11)), 2)
    if best == "unknown":
        confidence = 0.4
    summary = (
        f"Classified as {best} using deterministic keyword and source metadata rules."
    )
    return {
        "problem_type": best,
        "secondary_problem_types": secondaries,
        "confidence": confidence,
        "missing_data": missing_data,
        "reasoning_summary": summary,
    }


def _engine_descriptor(name: str, *, candidate: bool = False) -> dict[str, Any]:
    if candidate:
        return {
            "name": name,
            "status": "not_implemented",
            "execution": "not_executed",
        }
    return {
        "name": name,
        "status": "available",
        "execution": "not_executed_plan_only",
    }


def route_engines(classification: dict[str, Any]) -> dict[str, Any]:
    problem_type = str(classification.get("problem_type") or "unknown")
    route = ENGINE_ROUTES.get(problem_type, ENGINE_ROUTES["unknown"])
    recommended = [
        _engine_descriptor(name)
        for name in route.recommended
        if name in AVAILABLE_ENGINES
    ]
    candidates = [
        _engine_descriptor(name, candidate=True)
        for name in route.candidates
        if name in CANDIDATE_ENGINES
    ]
    return {
        "recommended_engines": recommended,
        "candidate_engines": candidates,
        "engine_plan": {
            "mode": "plan_only",
            "model_version": MODEL_VERSION,
            "notes": list(route.notes),
            "candidate_engines_executed": False,
            "available_engines_executed": False,
        },
    }


def build_decision_plan(
    normalized: dict[str, Any],
    classification: dict[str, Any],
    engine_route: dict[str, Any],
) -> dict[str, Any]:
    problem_type = str(classification["problem_type"])
    action_recommended = problem_type == "simple_action" and classification["confidence"] >= 0.5
    steps = [
        "Review linked evidence in the active workspace.",
        "Run recommended engines manually if more quantitative evidence is required.",
    ]
    if engine_route["candidate_engines"]:
        steps.append("Treat candidate engines as roadmap markers; they are not implemented.")
    if action_recommended:
        steps.append("Create a sandbox external action proposal for human approval.")
    if problem_type == "insufficient_data":
        steps = ["Collect missing evidence before proposing any action."]
    return {
        "mode": "advisory_plan_only",
        "problem_type": problem_type,
        "steps": steps,
        "evidence_refs": normalized.get("evidence_refs") or [],
        "next_human_decision": (
            "approve_or_reject_sandbox_proposal"
            if action_recommended
            else "review_or_collect_more_evidence"
        ),
        "action_recommended": action_recommended,
    }


def build_orchestration_plan(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    _validate_no_scope_fields(payload)
    normalized = normalize_signal(payload, source)
    classification = classify_problem(normalized)
    route = route_engines(classification)
    decision_plan = build_decision_plan(normalized, classification, route)
    safety_notes = [
        "Plan-only orchestrator: available engines were not executed automatically.",
        "Candidate engines are marked not_implemented and were not called.",
        "No external write-back, auto-approval or auto-execution is performed.",
    ]
    if decision_plan["action_recommended"]:
        safety_notes.append("Any external action proposal requires human approval.")
    return {
        **classification,
        **route,
        "decision_plan": decision_plan,
        "action_recommended": bool(decision_plan["action_recommended"]),
        "safety_notes": safety_notes,
        "evidence_refs": normalized.get("evidence_refs") or [],
    }


def _orchestration_id(
    *,
    workspace_id: str,
    payload: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    digest = sample_hash(
        {
            "model_version": MODEL_VERSION,
            "workspace_id": workspace_id,
            "source_type": payload.get("source_type"),
            "source_id": payload.get("source_id"),
            "problem_type": plan.get("problem_type"),
            "evidence_refs": plan.get("evidence_refs") or [],
            "title": payload.get("title"),
            "description": payload.get("description"),
            "metrics": payload.get("metrics") or {},
            "constraints": payload.get("constraints") or {},
        }
    )
    return "orch-" + digest


async def _load_source(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if source_type == "manual_fixture":
        if not _manual_fixture_allowed():
            raise DecisionOrchestratorError(403, "manual_fixture is disabled")
        return {
            "source_id": source_id,
            "title": payload.get("title"),
            "description": payload.get("description"),
            "metadata": {
                "metrics": payload.get("metrics") or {},
                "entities": payload.get("entities") or [],
                "constraints": payload.get("constraints") or {},
                "evidence_refs": payload.get("evidence_refs") or [],
            },
        }
    if source_type in {"control_room_item", "agent_alert"}:
        clause = "AND item_kind = 'agent_alert'" if source_type == "agent_alert" else ""
        row = await conn.fetchrow(
            f"""
            SELECT tenant_id, workspace_id, item_id AS source_id, item_kind,
                   title, severity, status, domain, source_dataset,
                   entity_kind, entity_id, entity_label, anomaly_type, metadata
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = $2
               {clause}
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        if not row:
            raise DecisionOrchestratorError(404, "orchestrator source not found")
        return _row_dict(row)
    if source_type == "intelligence_signal":
        row = await conn.fetchrow(
            """
            SELECT tenant_id, workspace_id, signal_id AS source_id, domain,
                   dataset, entity_kind, entity_id, entity_label, metric,
                   severity, signal_type, status, confidence, summary,
                   metadata
              FROM intelligence_signals
             WHERE workspace_id = $1
               AND signal_id = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        if not row:
            raise DecisionOrchestratorError(404, "orchestrator source not found")
        return _row_dict(row)
    if source_type == "monte_carlo_simulation":
        row = await conn.fetchrow(
            """
            SELECT tenant_id, workspace_id, simulation_id AS source_id,
                   source_type AS simulation_source_type, source_id AS simulation_source_id,
                   output_metric AS metric, distribution_summary, sensitivity,
                   evidence_refs, assumptions, option_comparison
              FROM monte_carlo_simulations
             WHERE workspace_id = $1
               AND simulation_id = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        if not row:
            raise DecisionOrchestratorError(404, "orchestrator source not found")
        data = _row_dict(row)
        data["metadata"] = {
            "distribution_summary": _json_obj(data.get("distribution_summary")),
            "sensitivity": _json_list(data.get("sensitivity")),
            "evidence_refs": _json_list(data.get("evidence_refs")),
            "assumptions": _json_obj(data.get("assumptions")),
            "option_comparison": _json_obj(data.get("option_comparison")),
        }
        data["summary"] = "Monte Carlo simulation result"
        return data
    if source_type == "calibration_observation":
        row = await conn.fetchrow(
            """
            SELECT tenant_id, workspace_id, observation_id AS source_id,
                   source_type AS observed_source_type, source_id AS observed_source_id,
                   predicted_metric AS metric, actual_status, horizon_days,
                   model_version, calibration_group, prior, posterior,
                   metrics, evidence_refs, explanation AS summary
              FROM calibration_observations
             WHERE workspace_id = $1
               AND (observation_id = $2 OR id::text = $2)
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        if not row:
            raise DecisionOrchestratorError(404, "orchestrator source not found")
        data = _row_dict(row)
        data["metadata"] = {
            "metrics": _json_obj(data.get("metrics")),
            "posterior": _json_obj(data.get("posterior")),
            "prior": _json_obj(data.get("prior")),
            "evidence_refs": _json_list(data.get("evidence_refs")),
        }
        return data
    raise DecisionOrchestratorError(422, "unsupported source_type")


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


def _external_action_payload(
    *,
    orchestration_id: str,
    source_type: str,
    source_id: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_type": "decision_orchestration_run",
        "source_id": orchestration_id,
        "action_type": "orchestrator_recommendation",
        "adapter_name": "sandbox",
        "payload": {
            "orchestration_id": orchestration_id,
            "problem_type": plan["problem_type"],
            "source_type": source_type,
            "source_id": source_id,
            "decision_plan": plan["decision_plan"],
            "evidence_refs": plan.get("evidence_refs") or [],
            "recommended_engines": plan["recommended_engines"],
            "candidate_engines": plan["candidate_engines"],
        },
        "dry_run_payload": {},
        "idempotency_key": f"orchestrator:{orchestration_id}:propose",
        "metadata": {
            "created_by_orchestrator": True,
            "orchestration_id": orchestration_id,
            "requires_human_approval": True,
        },
    }


async def orchestrate(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload or {})
    _validate_no_scope_fields(body)
    source_type = str(body.get("source_type") or "").strip()
    source_id = str(body.get("source_id") or "").strip()
    if source_type not in SOURCE_TYPES:
        raise DecisionOrchestratorError(422, "unsupported source_type")
    if not source_id:
        raise DecisionOrchestratorError(422, "source_id is required")
    body["source_type"] = source_type
    body["source_id"] = source_id

    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        source = await _load_source(
            conn,
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id,
            payload=body,
        )
        plan = build_orchestration_plan(body, source)
        orchestration_id = _orchestration_id(
            workspace_id=workspace_id,
            payload=body,
            plan=plan,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO decision_orchestration_runs (
                orchestration_id, tenant_id, workspace_id, source_type, source_id,
                problem_type, secondary_problem_types, confidence,
                recommended_engines, candidate_engines, engine_plan,
                decision_plan, action_recommended, external_action_id,
                reasoning_summary, safety_notes, missing_data, created_by
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7::jsonb, $8,
                $9::jsonb, $10::jsonb, $11::jsonb,
                $12::jsonb, $13, NULL,
                $14, $15::jsonb, $16::jsonb, $17
            )
            ON CONFLICT (workspace_id, orchestration_id) DO UPDATE
            SET problem_type = EXCLUDED.problem_type,
                secondary_problem_types = EXCLUDED.secondary_problem_types,
                confidence = EXCLUDED.confidence,
                recommended_engines = EXCLUDED.recommended_engines,
                candidate_engines = EXCLUDED.candidate_engines,
                engine_plan = EXCLUDED.engine_plan,
                decision_plan = EXCLUDED.decision_plan,
                action_recommended = EXCLUDED.action_recommended,
                reasoning_summary = EXCLUDED.reasoning_summary,
                safety_notes = EXCLUDED.safety_notes,
                missing_data = EXCLUDED.missing_data,
                updated_at = NOW()
            RETURNING *
            """,
            orchestration_id,
            tenant_id,
            workspace_id,
            source_type,
            source_id,
            plan["problem_type"],
            json_dumps(plan["secondary_problem_types"]),
            float(plan["confidence"]),
            json_dumps(plan["recommended_engines"]),
            json_dumps(plan["candidate_engines"]),
            json_dumps(plan["engine_plan"]),
            json_dumps(plan["decision_plan"]),
            bool(plan["action_recommended"]),
            plan["reasoning_summary"],
            json_dumps(plan["safety_notes"]),
            json_dumps(plan["missing_data"]),
            _actor_id(user),
        )

    run = _serialize_run(row)
    if bool(run.get("action_recommended")) and action_creation_enabled():
        action_response = await external_actions.propose(
            user,
            _external_action_payload(
                orchestration_id=run["orchestration_id"],
                source_type=source_type,
                source_id=source_id,
                plan=plan,
            ),
        )
        action = action_response.get("action") or {}
        external_action_id = action.get("id")
        if external_action_id:
            async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
                row = await conn.fetchrow(
                    """
                    UPDATE decision_orchestration_runs
                       SET external_action_id = $3,
                           updated_at = NOW()
                     WHERE workspace_id = $1
                       AND orchestration_id = $2
                     RETURNING *
                    """,
                    workspace_id,
                    run["orchestration_id"],
                    external_action_id,
                )
            run = _serialize_run(row)
            run["external_action"] = action
    return {"orchestration": run}


async def get_orchestration(user: dict, orchestration_id: str) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        row = await conn.fetchrow(
            """
            SELECT *
              FROM decision_orchestration_runs
             WHERE workspace_id = $1
               AND orchestration_id = $2
            """,
            workspace_id,
            orchestration_id,
        )
    if not row:
        raise DecisionOrchestratorError(404, "decision orchestration not found")
    return {"orchestration": _serialize_run(row)}


async def list_orchestrations(
    user: dict,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    where = ["workspace_id = $1"]
    params: list[Any] = []
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        params.append(workspace_id)
        if source_type:
            if source_type not in SOURCE_TYPES:
                raise DecisionOrchestratorError(422, "unsupported source_type")
            params.append(source_type)
            where.append(f"source_type = ${len(params)}")
        if source_id:
            params.append(source_id)
            where.append(f"source_id = ${len(params)}")
        params.append(int(max(1, min(limit, 250))))
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM decision_orchestration_runs
             WHERE {' AND '.join(where)}
             ORDER BY created_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"orchestrations": [_serialize_run(row) for row in rows]}

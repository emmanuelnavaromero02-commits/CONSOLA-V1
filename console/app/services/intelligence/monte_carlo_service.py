from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import market_context, monte_carlo, monte_carlo_repository
from app.services.intelligence.utils import json_dumps, public_json


SOURCE_TYPES = {
    "signal",
    "decision_option",
    "manual_fixture",
    "backtest_case",
    "wisdom_bit",
}
FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}


def _actor_id(user: dict | None) -> int | None:
    raw = (user or {}).get("id")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _synthetic_allowed() -> bool:
    app_env = os.environ.get("APP_ENV", "production").strip().lower()
    if app_env in {"development", "dev", "test", "testing"}:
        return True
    return os.environ.get("MONTE_CARLO_ALLOW_SYNTHETIC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_evidence_refs(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise HTTPException(422, "evidence_refs must be a list with at most 20 items")
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(422, "evidence_refs entries must be objects")
        ref_type = str(item.get("type") or "").strip()
        ref_id = str(item.get("id") or "").strip()
        if not ref_type or not ref_id or len(ref_type) > 64 or len(ref_id) > 256:
            raise HTTPException(422, "evidence_refs entries require short type and id")
        refs.append({"type": ref_type, "id": ref_id})
    return refs


def _simulation_id(
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    payload: dict[str, Any],
    result_hash: str,
) -> str:
    raw = monte_carlo.canonical_json(
        {
            "workspace_id": workspace_id,
            "source_type": source_type,
            "source_id": source_id,
            "payload": payload,
            "result_hash": result_hash,
        }
    )
    return "mc-" + monte_carlo.reproducibility_hash({"simulation": raw})[:32]


async def _source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    if source_type == "manual_fixture":
        return True
    if source_type == "wisdom_bit":
        return source_id.strip().upper() == "WB-TALENTO"
    if source_type == "signal":
        value = await conn.fetchval(
            """
            SELECT 1
              FROM intelligence_signals
             WHERE workspace_id = $1
               AND signal_id = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return bool(value)
    if source_type == "decision_option":
        value = await conn.fetchval(
            """
            SELECT 1
              FROM decision_options
             WHERE workspace_id = $1
               AND (id::text = $2 OR option_id = $2)
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return bool(value)
    if source_type == "backtest_case":
        exists = await conn.fetchval("SELECT to_regclass('public.backtest_runs')")
        if not exists:
            return False
        value = await conn.fetchval(
            """
            SELECT 1
              FROM backtest_runs
             WHERE workspace_id = $1
               AND (id::text = $2 OR run_ref = $2)
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return bool(value)
    return False


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    if any(key in clean for key in FORBIDDEN_SCOPE_KEYS):
        raise HTTPException(422, "tenant/workspace/security_context are not accepted")
    input_variables = clean.get("input_variables") or {}
    if isinstance(input_variables, dict) and FORBIDDEN_SCOPE_KEYS & set(input_variables):
        raise HTTPException(422, "scope variables are not accepted")
    for option in clean.get("options") or []:
        if isinstance(option, dict):
            option_variables = option.get("input_variables") or {}
            if isinstance(option_variables, dict) and FORBIDDEN_SCOPE_KEYS & set(option_variables):
                raise HTTPException(422, "scope variables are not accepted")
    source_type = str(clean.get("source_type") or "").strip()
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "unsupported source_type")
    source_id = str(clean.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(422, "source_id is required")
    if source_type == "manual_fixture" and not _synthetic_allowed():
        raise HTTPException(403, "manual_fixture is disabled outside development/test")
    clean["source_type"] = source_type
    clean["source_id"] = source_id
    clean["use_external_market_context"] = bool(clean.get("use_external_market_context"))
    clean["evidence_refs"] = _validate_evidence_refs(clean.get("evidence_refs"))
    return clean


async def run_simulation(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    clean = _validate_payload(payload)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        if not await _source_exists(
            conn,
            workspace_id=workspace_id,
            source_type=clean["source_type"],
            source_id=clean["source_id"],
        ):
            raise HTTPException(404, "monte carlo source not found")
        clean = await market_context.resolve_market_context_inputs(clean, user)
        try:
            result = monte_carlo.run_monte_carlo(clean)
        except monte_carlo.MonteCarloValidationError as exc:
            raise HTTPException(422, str(exc)) from exc

        simulation_id = _simulation_id(
            workspace_id=workspace_id,
            source_type=clean["source_type"],
            source_id=clean["source_id"],
            payload=clean,
            result_hash=result["reproducibility_hash"],
        )
        row = await conn.fetchrow(
            """
            INSERT INTO monte_carlo_simulations (
                simulation_id, tenant_id, workspace_id, source_type, source_id,
                horizon_days, iterations, seed, model_version, input_variables,
                assumptions, output_metric, breach_threshold, breach_direction,
                distribution_summary, sensitivity, option_comparison,
                evidence_refs, reproducibility_hash, created_by
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10::jsonb,
                $11::jsonb, $12, $13, $14,
                $15::jsonb, $16::jsonb, $17::jsonb,
                $18::jsonb, $19, $20
            )
            ON CONFLICT (workspace_id, simulation_id) DO UPDATE
            SET iterations = EXCLUDED.iterations,
                seed = EXCLUDED.seed,
                model_version = EXCLUDED.model_version,
                input_variables = EXCLUDED.input_variables,
                assumptions = EXCLUDED.assumptions,
                output_metric = EXCLUDED.output_metric,
                breach_threshold = EXCLUDED.breach_threshold,
                breach_direction = EXCLUDED.breach_direction,
                distribution_summary = EXCLUDED.distribution_summary,
                sensitivity = EXCLUDED.sensitivity,
                option_comparison = EXCLUDED.option_comparison,
                evidence_refs = EXCLUDED.evidence_refs,
                reproducibility_hash = EXCLUDED.reproducibility_hash,
                updated_at = NOW()
            RETURNING *
            """,
            simulation_id,
            tenant_id,
            workspace_id,
            clean["source_type"],
            clean["source_id"],
            int(clean.get("horizon_days") or 30),
            int(clean.get("iterations") or monte_carlo.DEFAULT_ITERATIONS),
            int(clean.get("seed") or 0),
            result["model_version"],
            json_dumps(result["normalized_input_variables"]),
            json_dumps(clean.get("assumptions") or {}),
            str(clean.get("output_metric") or "net_value"),
            clean.get("breach_threshold"),
            result["distribution_summary"].get("breach_direction"),
            json_dumps(result["distribution_summary"]),
            json_dumps(result["sensitivity"]),
            json_dumps(result.get("option_comparison") or {}),
            json_dumps(clean["evidence_refs"]),
            result["reproducibility_hash"],
            _actor_id(user),
        )
    row = await monte_carlo_repository.require_visible(pool, user, simulation_id)
    return {"simulation": public_json(dict(row))}


async def get_simulation(user: dict, simulation_id: str) -> dict[str, Any]:
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        row = await conn.fetchrow(
            """
            SELECT *
              FROM monte_carlo_simulations
             WHERE workspace_id = $1
               AND simulation_id = $2
            """,
            workspace_id,
            simulation_id,
        )
    if not row:
        raise HTTPException(404, "monte carlo simulation not found")
    return {"simulation": public_json(dict(row))}


async def list_simulations(
    user: dict,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    params: list[Any] = []
    where = ["workspace_id = $1"]
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        params.append(workspace_id)
        if source_type:
            if source_type not in SOURCE_TYPES:
                raise HTTPException(422, "unsupported source_type")
            params.append(source_type)
            where.append(f"source_type = ${len(params)}")
        if source_id:
            params.append(source_id)
            where.append(f"source_id = ${len(params)}")
        params.append(limit)
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM monte_carlo_simulations
             WHERE {' AND '.join(where)}
             ORDER BY updated_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"simulations": [public_json(dict(row)) for row in rows]}

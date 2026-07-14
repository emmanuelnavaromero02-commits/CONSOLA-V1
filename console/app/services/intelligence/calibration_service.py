from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import calibration
from app.services.intelligence.evidence_refs import (
    attach_external_evidence_metadata,
    normalize_evidence_refs,
)
from app.services.intelligence.utils import json_dumps, public_json


SOURCE_TYPES = {
    "monte_carlo_simulation",
    "decision_option",
    "prediction_outcome",
    "backtest_case",
    "manual_fixture",
}
FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}
DEFAULT_MODEL_VERSION = calibration.MODEL_VERSION


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
    return os.environ.get("CALIBRATION_ALLOW_SYNTHETIC", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _forbidden_path(value: Any, *, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            text_key = str(key)
            path = f"{prefix}.{text_key}" if prefix else text_key
            if text_key in FORBIDDEN_SCOPE_KEYS:
                return path
            nested = _forbidden_path(item, prefix=path)
            if nested:
                return nested
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            nested = _forbidden_path(item, prefix=f"{prefix}[{idx}]")
            if nested:
                return nested
    return None


def _short_text(value: Any, *, field: str, max_length: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text and required:
        raise HTTPException(422, f"{field} is required")
    if len(text) > max_length:
        raise HTTPException(422, f"{field} is too long")
    return text


def _validate_evidence_refs(value: Any) -> list[dict[str, str]]:
    return normalize_evidence_refs(value, max_items=20)


def _observed_at(value: Any) -> str:
    if value in (None, ""):
        return datetime.now(timezone.utc).isoformat()
    text = str(value).strip()
    if len(text) > 80:
        raise HTTPException(422, "observed_at is too long")
    parseable = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise HTTPException(422, "observed_at must be ISO-8601") from exc
    return text


def _calibration_group(source_type: str, value: Any) -> str:
    provided = str(value or "").strip()
    if provided:
        if len(provided) > 80:
            raise HTTPException(422, "calibration_group is too long")
        return provided
    return {
        "monte_carlo_simulation": "monte_carlo",
        "decision_option": "decision_option",
        "prediction_outcome": "prediction_outcome",
        "backtest_case": "backtest",
        "manual_fixture": "global",
    }[source_type]


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    forbidden = _forbidden_path(clean)
    if forbidden:
        raise HTTPException(422, f"scope fields are not accepted: {forbidden}")
    source_type = _short_text(clean.get("source_type"), field="source_type", max_length=80)
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "unsupported source_type")
    if source_type == "manual_fixture" and not _synthetic_allowed():
        raise HTTPException(403, "manual_fixture is disabled outside development/test")
    source_id = _short_text(clean.get("source_id"), field="source_id", max_length=256)
    model_version = _short_text(
        clean.get("model_version") or DEFAULT_MODEL_VERSION,
        field="model_version",
        max_length=120,
    )
    observed_at = _observed_at(clean.get("observed_at"))
    horizon_days = clean.get("horizon_days", 30)
    try:
        horizon_days = int(horizon_days)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "horizon_days must be an integer") from exc
    if horizon_days < 1 or horizon_days > 3650:
        raise HTTPException(422, "horizon_days must be between 1 and 3650")
    engine_payload = {
        "actual_status": clean.get("actual_status"),
        "predicted_metric": clean.get("predicted_metric"),
        "predicted_probability": clean.get("predicted_probability"),
        "predicted_value": clean.get("predicted_value"),
        "predicted_interval": clean.get("predicted_interval") or {},
        "actual_value": clean.get("actual_value"),
        "calibration_group": _calibration_group(source_type, clean.get("calibration_group")),
        "model_version": model_version,
    }
    try:
        calibration.normalize_observation(engine_payload)
    except calibration.CalibrationValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        **clean,
        **engine_payload,
        "source_type": source_type,
        "source_id": source_id,
        "model_version": model_version,
        "observed_at": observed_at,
        "horizon_days": horizon_days,
        "evidence_refs": _validate_evidence_refs(clean.get("evidence_refs")),
    }


def _json_obj(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_state(row: Any | None, *, group: str, model_version: str) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    metrics = _json_obj(data.get("metrics"), {})
    return {
        "calibration_group": group,
        "model_version": model_version,
        "prior": _json_obj(data.get("prior"), {"alpha": 1.0, "beta": 1.0}),
        "posterior": _json_obj(data.get("posterior"), {"alpha": 1.0, "beta": 1.0}),
        "metrics": metrics,
    }


def _parent_prior_source(group: str) -> str:
    if group.startswith("source_type:"):
        return "source_type"
    if group.startswith("global:"):
        return "global"
    return "fixed"


def _parent_group_candidates(group: str) -> list[str]:
    parts = group.split(":")
    if len(parts) >= 5 and parts[0] == "specific":
        return [
            calibration.source_type_calibration_group(parts[1], parts[-2]),
            calibration.global_calibration_group(parts[-2]),
        ]
    if len(parts) >= 4 and parts[0] == "source_type":
        return [calibration.global_calibration_group(parts[-2])]
    return []


async def _fetch_state(
    conn: Any,
    *,
    workspace_id: str,
    group: str,
    model_version: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT *
          FROM calibration_states
         WHERE workspace_id = $1
           AND calibration_group = $2
           AND model_version = $3
        """,
        workspace_id,
        group,
        model_version,
    )
    return _row_state(row, group=group, model_version=model_version)


async def _derived_prior_for_group(
    conn: Any,
    *,
    workspace_id: str,
    group: str,
    model_version: str,
    explicit_parent_group: str | None = None,
) -> dict[str, Any]:
    candidates = [explicit_parent_group] if explicit_parent_group else _parent_group_candidates(group)
    for parent_group in [item for item in candidates if item]:
        parent_state = await _fetch_state(
            conn,
            workspace_id=workspace_id,
            group=parent_group,
            model_version=model_version,
        )
        prior = calibration.derive_partial_pooling_prior(
            parent_state,
            parent_calibration_group=parent_group,
            prior_source=_parent_prior_source(parent_group),
        )
        if prior.get("partial_pooling_applied"):
            return prior
    return calibration.derive_partial_pooling_prior(None)


def _state_id(*, workspace_id: str, group: str, model_version: str) -> str:
    digest = calibration.reproducibility_hash(
        {"workspace_id": workspace_id, "group": group, "model_version": model_version}
    )
    return "cal-state-" + digest[:32]


def _observation_id(*, workspace_id: str, payload: dict[str, Any], result_hash: str) -> str:
    digest = calibration.reproducibility_hash(
        {"workspace_id": workspace_id, "payload": payload, "result_hash": result_hash}
    )
    return "cal-obs-" + digest[:32]


async def _source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
) -> bool:
    if source_type == "manual_fixture":
        return True
    if source_type == "monte_carlo_simulation":
        value = await conn.fetchval(
            """
            SELECT 1
              FROM monte_carlo_simulations
             WHERE workspace_id = $1
               AND simulation_id = $2
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
    if source_type == "prediction_outcome":
        value = await conn.fetchval(
            """
            SELECT 1
              FROM prediction_outcomes
             WHERE workspace_id = $1
               AND id::text = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return bool(value)
    if source_type == "backtest_case":
        exists = await conn.fetchval("SELECT to_regclass('public.backtest_results')")
        if not exists:
            return False
        value = await conn.fetchval(
            """
            SELECT 1
              FROM backtest_results
             WHERE workspace_id = $1
               AND id::text = $2
             LIMIT 1
            """,
            workspace_id,
            source_id,
        )
        return bool(value)
    return False


async def observe(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    clean = _validate_payload(payload)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        if not await _source_exists(
            conn,
            workspace_id=workspace_id,
            source_type=clean["source_type"],
            source_id=clean["source_id"],
        ):
            raise HTTPException(404, "calibration source not found")
        existing = await conn.fetchrow(
            """
            SELECT *
              FROM calibration_states
             WHERE workspace_id = $1
               AND calibration_group = $2
               AND model_version = $3
            """,
            workspace_id,
            clean["calibration_group"],
            clean["model_version"],
        )
        result = calibration.apply_observation(
            _row_state(
                existing,
                group=clean["calibration_group"],
                model_version=clean["model_version"],
            ),
            clean,
        )
        obs_id = _observation_id(
            workspace_id=workspace_id,
            payload=clean,
            result_hash=result["reproducibility_hash"],
        )
        state_id = _state_id(
            workspace_id=workspace_id,
            group=clean["calibration_group"],
            model_version=clean["model_version"],
        )
        observation_metrics = attach_external_evidence_metadata(
            result["metrics"],
            clean["evidence_refs"],
        )
        metrics = attach_external_evidence_metadata(
            result["state"]["metrics"],
            clean["evidence_refs"],
        )
        observation_row = await conn.fetchrow(
            """
            INSERT INTO calibration_observations (
                observation_id, tenant_id, workspace_id, source_type, source_id,
                predicted_metric, predicted_value, predicted_interval,
                predicted_probability, actual_value, actual_status, observed_at,
                horizon_days, model_version, calibration_group, prior, posterior,
                metrics, evidence_refs, explanation, reproducibility_hash, created_by
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::jsonb,
                $9, $10, $11, $12::timestamptz,
                $13, $14, $15, $16::jsonb, $17::jsonb,
                $18::jsonb, $19::jsonb, $20, $21, $22
            )
            ON CONFLICT (workspace_id, observation_id) DO UPDATE
            SET metrics = EXCLUDED.metrics,
                posterior = EXCLUDED.posterior,
                evidence_refs = EXCLUDED.evidence_refs,
                explanation = EXCLUDED.explanation,
                reproducibility_hash = EXCLUDED.reproducibility_hash
            RETURNING *
            """,
            obs_id,
            tenant_id,
            workspace_id,
            clean["source_type"],
            clean["source_id"],
            clean["predicted_metric"],
            clean.get("predicted_value"),
            json_dumps(clean.get("predicted_interval") or {}),
            clean.get("predicted_probability"),
            clean.get("actual_value"),
            clean["actual_status"],
            clean["observed_at"],
            clean["horizon_days"],
            clean["model_version"],
            clean["calibration_group"],
            json_dumps(result["observation_prior"]),
            json_dumps(result["observation_posterior"]),
            json_dumps(observation_metrics),
            json_dumps(clean["evidence_refs"]),
            result["explanation"],
            result["reproducibility_hash"],
            _actor_id(user),
        )
        state_row = await _upsert_state(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            state_id=state_id,
            group=clean["calibration_group"],
            model_version=clean["model_version"],
            prior=result["state"]["prior"],
            posterior=result["state"]["posterior"],
            metrics=metrics,
            reproducibility_hash=result["reproducibility_hash"],
            last_observed_at=clean["observed_at"],
        )
    return {
        "observation": public_json(dict(observation_row)),
        "state": public_json(dict(state_row)),
    }


async def _upsert_state(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    state_id: str,
    group: str,
    model_version: str,
    prior: dict[str, Any],
    posterior: dict[str, Any],
    metrics: dict[str, Any],
    reproducibility_hash: str,
    last_observed_at: str | None = None,
) -> Any:
    return await conn.fetchrow(
        """
        INSERT INTO calibration_states (
            state_id, tenant_id, workspace_id, calibration_group, model_version,
            prior, posterior, metrics, sample_count, hit_count, miss_count,
            partial_count, unknown_count, brier_score, mae, rmse,
            coverage_p10_p90, calibration_error, confidence_score,
            last_observed_at, reproducibility_hash
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6::jsonb, $7::jsonb, $8::jsonb, $9, $10, $11,
            $12, $13, $14, $15, $16,
            $17, $18, $19,
            $20::timestamptz, $21
        )
        ON CONFLICT (workspace_id, calibration_group, model_version) DO UPDATE
        SET prior = EXCLUDED.prior,
            posterior = EXCLUDED.posterior,
            metrics = EXCLUDED.metrics,
            sample_count = EXCLUDED.sample_count,
            hit_count = EXCLUDED.hit_count,
            miss_count = EXCLUDED.miss_count,
            partial_count = EXCLUDED.partial_count,
            unknown_count = EXCLUDED.unknown_count,
            brier_score = EXCLUDED.brier_score,
            mae = EXCLUDED.mae,
            rmse = EXCLUDED.rmse,
            coverage_p10_p90 = EXCLUDED.coverage_p10_p90,
            calibration_error = EXCLUDED.calibration_error,
            confidence_score = EXCLUDED.confidence_score,
            last_observed_at = COALESCE(EXCLUDED.last_observed_at, calibration_states.last_observed_at),
            reproducibility_hash = EXCLUDED.reproducibility_hash,
            updated_at = NOW()
        RETURNING *
        """,
        state_id,
        tenant_id,
        workspace_id,
        group,
        model_version,
        json_dumps(prior),
        json_dumps(posterior),
        json_dumps(metrics),
        int(metrics.get("sample_count") or 0),
        int(metrics.get("hit_count") or 0),
        int(metrics.get("miss_count") or 0),
        int(metrics.get("partial_count") or 0),
        int(metrics.get("unknown_count") or 0),
        metrics.get("brier_score"),
        metrics.get("mae"),
        metrics.get("rmse"),
        metrics.get("coverage_p10_p90"),
        metrics.get("calibration_error"),
        metrics.get("confidence_score"),
        last_observed_at,
        reproducibility_hash,
    )


async def recompute(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    forbidden = _forbidden_path(clean)
    if forbidden:
        raise HTTPException(422, f"scope fields are not accepted: {forbidden}")
    group = _short_text(clean.get("calibration_group"), field="calibration_group", max_length=80)
    model_version = _short_text(
        clean.get("model_version") or DEFAULT_MODEL_VERSION,
        field="model_version",
        max_length=120,
    )
    parent_group = _short_text(
        clean.get("parent_calibration_group"),
        field="parent_calibration_group",
        max_length=80,
        required=False,
    ) or None
    source_type = str(clean.get("source_type") or "").strip() or None
    source_id = str(clean.get("source_id") or "").strip() or None
    if source_type and source_type not in SOURCE_TYPES:
        raise HTTPException(422, "unsupported source_type")
    limit = int(clean.get("limit") or 5000)
    if limit < 1 or limit > 10_000:
        raise HTTPException(422, "limit must be between 1 and 10000")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        params: list[Any] = [workspace_id, group, model_version]
        where = ["workspace_id = $1", "calibration_group = $2", "model_version = $3"]
        if source_type:
            params.append(source_type)
            where.append(f"source_type = ${len(params)}")
        if source_id:
            params.append(source_id)
            where.append(f"source_id = ${len(params)}")
        params.append(limit)
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM calibration_observations
             WHERE {' AND '.join(where)}
             ORDER BY observed_at ASC, id ASC
             LIMIT ${len(params)}
            """,
            *params,
        )
        row_dicts = [dict(row) for row in rows]
        observations = [_observation_from_row(row) for row in row_dicts]
        prior = await _derived_prior_for_group(
            conn,
            workspace_id=workspace_id,
            group=group,
            model_version=model_version,
            explicit_parent_group=parent_group,
        )
        state = calibration.recompute_state(
            observations,
            calibration_group=group,
            model_version=model_version,
            prior=prior,
        )
        evidence_refs: list[dict[str, str]] = []
        for row in row_dicts:
            evidence_refs.extend(normalize_evidence_refs(_json_obj(row.get("evidence_refs"), [])))
        state["metrics"] = attach_external_evidence_metadata(
            state.get("metrics") or {},
            evidence_refs,
        )
        state_row = await _upsert_state(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            state_id=_state_id(
                workspace_id=workspace_id,
                group=group,
                model_version=model_version,
            ),
            group=group,
            model_version=model_version,
            prior=state["prior"],
            posterior=state["posterior"],
            metrics=state["metrics"],
            reproducibility_hash=state.get("reproducibility_hash")
            or calibration.reproducibility_hash(state),
            last_observed_at=None,
        )
    return {"state": public_json(dict(state_row)), "observations_recomputed": len(observations)}


async def get_state_map_for_live_calibration(
    user: dict,
    groups: list[str] | set[str] | tuple[str, ...],
    *,
    model_version: str | None = None,
) -> dict[str, dict[str, Any]]:
    clean_groups = sorted({str(group).strip() for group in groups if str(group or "").strip()})
    if not clean_groups:
        return {}
    version = str(model_version or DEFAULT_MODEL_VERSION)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        rows = await conn.fetch(
            """
            SELECT *
              FROM calibration_states
             WHERE workspace_id = $1
               AND calibration_group = ANY($2::text[])
               AND model_version = $3
            """,
            workspace_id,
            clean_groups,
            version,
        )
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        group = str(data.get("calibration_group") or "")
        if not group:
            continue
        state = _row_state(data, group=group, model_version=version)
        if state:
            states[group] = state
    return states


def _observation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "actual_status": row.get("actual_status"),
        "predicted_metric": row.get("predicted_metric"),
        "predicted_probability": row.get("predicted_probability"),
        "predicted_value": row.get("predicted_value"),
        "predicted_interval": _json_obj(row.get("predicted_interval"), {}),
        "actual_value": row.get("actual_value"),
        "calibration_group": row.get("calibration_group") or "global",
        "model_version": row.get("model_version") or DEFAULT_MODEL_VERSION,
    }


async def get_state(
    user: dict,
    *,
    calibration_group: str | None = None,
    model_version: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if limit < 1 or limit > 250:
        raise HTTPException(422, "limit must be between 1 and 250")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        params: list[Any] = [workspace_id]
        where = ["workspace_id = $1"]
        if calibration_group:
            params.append(calibration_group)
            where.append(f"calibration_group = ${len(params)}")
        if model_version:
            params.append(model_version)
            where.append(f"model_version = ${len(params)}")
        params.append(limit)
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM calibration_states
             WHERE {' AND '.join(where)}
             ORDER BY updated_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"states": [public_json(dict(row)) for row in rows]}


async def list_observations(
    user: dict,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    calibration_group: str | None = None,
    model_version: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if source_type and source_type not in SOURCE_TYPES:
        raise HTTPException(422, "unsupported source_type")
    if limit < 1 or limit > 250:
        raise HTTPException(422, "limit must be between 1 and 250")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        params: list[Any] = [workspace_id]
        where = ["workspace_id = $1"]
        if source_type:
            params.append(source_type)
            where.append(f"source_type = ${len(params)}")
        if source_id:
            params.append(source_id)
            where.append(f"source_id = ${len(params)}")
        if calibration_group:
            params.append(calibration_group)
            where.append(f"calibration_group = ${len(params)}")
        if model_version:
            params.append(model_version)
            where.append(f"model_version = ${len(params)}")
        params.append(limit)
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM calibration_observations
             WHERE {' AND '.join(where)}
             ORDER BY observed_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"observations": [public_json(dict(row)) for row in rows]}

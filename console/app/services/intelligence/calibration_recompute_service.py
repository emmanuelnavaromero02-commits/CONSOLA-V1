from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import calibration
from app.services.intelligence import engine_policy
from app.services.intelligence.calibration_lock import lock_calibration_group
from app.services.intelligence.calibration_recompute_batch import (
    BatchFailure,
    load_complete_batch,
)
from app.services.intelligence.calibration_state_repository import (
    _derived_prior_for_group,
    _json_obj,
    _row_state,
    _state_payload,
    _state_id,
    _upsert_state,
)
from app.services.intelligence.calibration_validation_service import (
    DEFAULT_MODEL_VERSION,
    SOURCE_TYPES,
    _forbidden_path,
    _short_text,
    _synthetic_allowed,
)
from app.services.intelligence.evidence_refs import (
    attach_external_evidence_metadata,
    normalize_evidence_refs,
)
from app.services.intelligence.utils import public_json


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


async def recompute(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    if not engine_policy.math_engines_enabled():
        raise HTTPException(403, engine_policy.PAUSED_REASON)
    clean = dict(payload or {})
    if "model_version" in clean:
        raise HTTPException(422, "model_version is server-owned")
    if "parent_calibration_group" in clean:
        raise HTTPException(422, "parent_calibration_group is server-owned")
    if (
        str(clean.get("source_type") or "").strip() == "manual_fixture"
        and not _synthetic_allowed()
    ):
        raise HTTPException(403, "manual_fixture recompute requires a local APP_ENV")
    if "source_type" in clean or "source_id" in clean:
        raise HTTPException(422, "filtered recompute is not accepted")
    forbidden = _forbidden_path(clean)
    if forbidden:
        raise HTTPException(422, f"scope fields are not accepted: {forbidden}")
    group = _short_text(
        clean.get("calibration_group"), field="calibration_group", max_length=80
    )
    model_version = DEFAULT_MODEL_VERSION
    source_type = None
    source_id = None
    allow_manual = _synthetic_allowed()
    if source_type == "manual_fixture" and not allow_manual:
        raise HTTPException(403, "manual_fixture recompute requires a local APP_ENV")
    limit = int(clean.get("limit") or 5000)
    if limit < 1 or limit > 10_000:
        raise HTTPException(422, "limit must be between 1 and 10000")
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        await lock_calibration_group(
            conn,
            workspace_id=workspace_id,
            group=group,
            model_version=model_version,
        )
        try:
            row_dicts, batch_metrics = await load_complete_batch(
                conn,
                workspace_id=workspace_id,
                group=group,
                model_version=model_version,
                source_type=source_type,
                source_id=source_id,
                operational_limit=limit,
                allow_manual=allow_manual,
                tenant_id=tenant_id,
            )
        except BatchFailure as exc:
            raise HTTPException(
                409,
                {
                    "status": "unavailable",
                    "reason": exc.reason,
                    "eligible_total": exc.eligible_total,
                    "operational_limit": exc.operational_limit,
                    "complete": False,
                },
            ) from exc
        if not row_dicts:
            raise HTTPException(
                409,
                {"status": "insufficient_data", **batch_metrics},
            )
        observations = [_observation_from_row(row) for row in row_dicts]
        prior = await _derived_prior_for_group(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            group=group,
            model_version=model_version,
        )
        state = calibration.recompute_state(
            observations,
            calibration_group=group,
            model_version=model_version,
            prior=prior,
        )
        evidence_refs: list[dict[str, str]] = []
        for row in row_dicts:
            evidence_refs.extend(
                normalize_evidence_refs(
                    _json_obj(row.get("evidence_refs"), []),
                    allow_server_dataset_rows=True,
                )
            )
        state["metrics"] = attach_external_evidence_metadata(
            state.get("metrics") or {},
            evidence_refs,
        )
        state["metrics"].update(batch_metrics)
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
            last_observed_at=max(str(row.get("observed_at") or "") for row in row_dicts)
            or None,
        )
    return {
        "state": public_json(_state_payload(state_row)),
        "observations_recomputed": len(observations),
    }


async def get_state_map_for_live_calibration(
    user: dict,
    groups: list[str] | set[str] | tuple[str, ...],
    *,
    model_version: str | None = None,
) -> dict[str, dict[str, Any]]:
    clean_groups = sorted(
        {str(group).strip() for group in groups if str(group or "").strip()}
    )
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
    return {"states": [public_json(_state_payload(row)) for row in rows]}


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
        for value, column in (
            (source_type, "source_type"),
            (source_id, "source_id"),
            (calibration_group, "calibration_group"),
            (model_version, "model_version"),
        ):
            if value:
                params.append(value)
                where.append(f"{column} = ${len(params)}")
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


__all__ = (
    "get_state",
    "get_state_map_for_live_calibration",
    "list_observations",
    "recompute",
)

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db_for_user
from app.services.intelligence import calibration
from app.services.intelligence.calibration_lock import lock_calibration_group
from app.services.intelligence.calibration_state_repository import (
    _observation_id,
    _row_state,
    _source_exists,
    _state_id,
    _upsert_state,
)
from app.services.intelligence.calibration_validation_service import (
    _actor_id,
    _synthetic_allowed,
    _validate_payload,
)
from app.services.intelligence.evidence_refs import attach_external_evidence_metadata
from app.services.intelligence.utils import json_dumps, public_json


async def observe(user: dict, payload: dict[str, Any]) -> dict[str, Any]:
    clean = _validate_payload(payload)
    pool = await auth.pool()
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        await lock_calibration_group(
            conn,
            workspace_id=workspace_id,
            group=clean["calibration_group"],
            model_version=clean["model_version"],
        )
        if not await _source_exists(
            conn,
            workspace_id=workspace_id,
            source_type=clean["source_type"],
            source_id=clean["source_id"],
            allow_manual=_synthetic_allowed(),
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
        processed_total = int(metrics.get("sample_count") or 0)
        metrics.update(
            eligible_total=processed_total,
            processed_total=processed_total,
            skipped_total=0,
            skipped_by_reason={},
            complete=True,
            provenance_complete=True,
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


__all__ = ("observe",)

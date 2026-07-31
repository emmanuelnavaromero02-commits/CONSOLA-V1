from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.intelligence import calibration
from app.services.intelligence.calibration_source_validation import (
    calibration_source_exists,
)
from app.services.intelligence.utils import json_dumps


def _json_obj(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _row_state(
    row: Any | None, *, group: str, model_version: str
) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    metrics = _json_obj(data.get("metrics"), {})
    if (
        metrics.get("complete") is not True
        or metrics.get("provenance_complete") is not True
    ):
        return None
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
    candidates = (
        [explicit_parent_group]
        if explicit_parent_group
        else _parent_group_candidates(group)
    )
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


@dataclass(frozen=True)
class ObservationIdentity:
    observation_id: str
    idempotency_key: str
    evidence_digest: str
    identity_payload: dict[str, Any]


def _observation_identity(
    *, workspace_id: str, payload: dict[str, Any]
) -> ObservationIdentity:
    identity_payload = {
        "workspace_id": workspace_id,
        "source_type": payload["source_type"],
        "source_id": payload["source_id"],
        "model_version": payload["model_version"],
        "calibration_group": payload["calibration_group"],
        "predicted_metric": payload["predicted_metric"],
    }
    evidence_payload = {
        key: payload.get(key)
        for key in (
            "source_type",
            "source_id",
            "predicted_metric",
            "predicted_probability",
            "predicted_value",
            "predicted_interval",
            "actual_value",
            "actual_status",
            "observed_at",
            "horizon_days",
            "model_version",
            "calibration_group",
            "evidence_refs",
            "input_classification",
        )
    }
    key = "cal-obs-" + calibration.reproducibility_hash(identity_payload)[:32]
    return ObservationIdentity(
        observation_id=key,
        idempotency_key=key,
        evidence_digest=calibration.reproducibility_hash(evidence_payload),
        identity_payload=identity_payload,
    )


def _observation_id(*, workspace_id: str, payload: dict[str, Any]) -> str:
    return _observation_identity(
        workspace_id=workspace_id, payload=payload
    ).observation_id


async def _source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    allow_manual: bool = False,
) -> bool:
    return await calibration_source_exists(
        conn,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        allow_manual=allow_manual,
    )


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
            ($20::text)::timestamptz, $21
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


__all__ = (
    "_derived_prior_for_group",
    "_json_obj",
    "_observation_identity",
    "_observation_id",
    "ObservationIdentity",
    "_row_state",
    "_source_exists",
    "_state_id",
    "_upsert_state",
)

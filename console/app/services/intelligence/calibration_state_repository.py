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
    sample_count = int(metrics.get("sample_count") or data.get("sample_count") or 0)
    processed_total = int(metrics.get("processed_total") or 0)
    eligible_total = int(metrics.get("eligible_total") or 0)
    skipped_total = int(metrics.get("skipped_total") or 0)
    if (
        metrics.get("complete") is not True
        or metrics.get("provenance_complete") is not True
        or metrics.get("binary_evaluation_complete") is not True
        or sample_count <= 0
        or processed_total != sample_count
        or eligible_total != processed_total
        or skipped_total != 0
    ):
        return None
    return {
        "calibration_group": group,
        "model_version": model_version,
        "prior": _json_obj(data.get("prior"), {"alpha": 1.0, "beta": 1.0}),
        "posterior": _json_obj(data.get("posterior"), {"alpha": 1.0, "beta": 1.0}),
        "metrics": metrics,
    }


def _state_payload(row: Any) -> dict[str, Any]:
    data = dict(row)
    for field in ("prior", "posterior", "metrics"):
        data[field] = _json_obj(data.get(field), {})
    return data


def _parent_prior_source(group: str) -> str:
    if group.startswith("source_type:"):
        return "source_type"
    if group.startswith("global:"):
        return "global"
    return "fixed"


async def _fetch_state(
    conn: Any,
    *,
    tenant_id: str | None,
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
           AND tenant_id IS NOT DISTINCT FROM $4::uuid
        """,
        workspace_id,
        group,
        model_version,
        tenant_id,
    )
    return _row_state(row, group=group, model_version=model_version)


async def _derived_prior_for_group(
    conn: Any,
    *,
    tenant_id: str | None = None,
    workspace_id: str,
    group: str,
    model_version: str,
) -> dict[str, Any]:
    parent_group = await conn.fetchval(
        """
        SELECT parent_group
          FROM calibration_group_hierarchy
         WHERE workspace_id = $1
           AND tenant_id IS NOT DISTINCT FROM $2::uuid
           AND child_group = $3
           AND model_version = $4
        """,
        workspace_id,
        tenant_id,
        group,
        model_version,
    )
    if parent_group:
        parent_state = await _fetch_state(
            conn,
            tenant_id=tenant_id,
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
            "evaluation_rule_version",
            "evaluated_at",
            "evaluated_by",
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
    payload = {
        "state_id": state_id,
        "calibration_group": group,
        "model_version": model_version,
        "prior": prior,
        "posterior": posterior,
        "metrics": metrics,
        "reproducibility_hash": reproducibility_hash,
        "last_observed_at": last_observed_at,
    }
    return await conn.fetchrow(
        "SELECT * FROM public.upsert_calibration_state($1::jsonb)",
        json_dumps(payload),
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

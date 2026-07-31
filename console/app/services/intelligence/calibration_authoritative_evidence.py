from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException

from app.services.control_room.business_runtime_evidence import (
    canonical_runtime_row_reference,
)
from app.services.intelligence import calibration
from app.services.intelligence.source_provenance_policy import (
    metadata,
    observed_signal,
)


_CLAIM_FIELDS = (
    "predicted_metric",
    "predicted_probability",
    "predicted_value",
    "predicted_interval",
    "actual_value",
    "actual_status",
)
_MISMATCH = "calibration claims do not match authoritative outcome"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise HTTPException(409, "authoritative outcome is incomplete")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(409, "authoritative outcome is incomplete") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _verified_refs(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    signal_metadata = metadata(row.get("signal_metadata"))
    refs: list[dict[str, Any]] = []
    for item in signal_metadata.get("evidence_refs") or []:
        if isinstance(item, Mapping):
            canonical = canonical_runtime_row_reference(item)
            if canonical is not None:
                refs.append({**canonical, "id": canonical["source_record_id"]})
    evidence_pack_id = str(row.get("evidence_pack_id") or "").strip()
    if evidence_pack_id:
        refs.append({"type": "evidence_pack", "id": evidence_pack_id})
    return refs


def _same_claim(claim: Any, authoritative: Any) -> bool:
    if isinstance(authoritative, float):
        return _number(claim) == authoritative
    return claim == authoritative


def _assert_client_claims(
    payload: dict[str, Any], authoritative: dict[str, Any]
) -> None:
    for field in _CLAIM_FIELDS:
        if field not in payload or payload.get(field) in (None, {}):
            continue
        if not _same_claim(payload[field], authoritative.get(field)):
            raise HTTPException(422, _MISMATCH)
    for field in ("observed_at", "horizon_days", "calibration_group"):
        if field not in payload or payload.get(field) in (None, ""):
            continue
        claimed = payload[field]
        expected = authoritative[field]
        if field == "observed_at":
            claimed = _timestamp(claimed)
        if claimed != expected:
            raise HTTPException(422, _MISMATCH)
    if payload.get("evidence_refs"):
        raise HTTPException(422, _MISMATCH)


def _manual_observation(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    clean["model_version"] = calibration.MODEL_VERSION
    clean["calibration_group"] = str(clean.get("calibration_group") or "global")
    clean["observed_at"] = (
        _timestamp(clean["observed_at"])
        if clean.get("observed_at")
        else datetime.now(timezone.utc).isoformat()
    )
    clean["horizon_days"] = int(clean.get("horizon_days") or 30)
    clean["evidence_refs"] = list(clean.get("evidence_refs") or [])
    clean["input_classification"] = "scenario_assumption"
    return clean


async def resolve_authoritative_observation(
    conn: Any,
    *,
    workspace_id: str,
    payload: dict[str, Any],
    allow_manual: bool,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    source_type = str(payload.get("source_type") or "").strip()
    if source_type == "manual_fixture":
        if not allow_manual:
            raise HTTPException(
                403, "manual_fixture requires an explicit local APP_ENV"
            )
        return _manual_observation(payload)
    if source_type != "prediction_outcome":
        raise HTTPException(404, "calibration source not found")

    row = await conn.fetchrow(
        """
        SELECT outcome.id::text AS outcome_id,
               outcome.tenant_id::text AS outcome_tenant_id,
               outcome.signal_id, outcome.option_id, outcome.action_taken,
               outcome.actual_value,
               outcome.metadata AS outcome_metadata,
               outcome.created_at AS outcome_created_at,
               signal.metric,
               signal.predicted_value AS signal_predicted_value,
               signal.signal_subtype,
               signal.metadata->>'source_system' AS source_system,
               signal.metadata->>'source_dataset' AS source_dataset,
               signal.metadata->>'evidence_pack_id' AS evidence_pack_id,
               signal.metadata->>'prediction_horizon_days' AS prediction_horizon_days,
               signal.metadata AS signal_metadata,
               CASE WHEN outcome.option_id IS NULL THEN TRUE ELSE EXISTS (
                   SELECT 1 FROM decision_options option
                    WHERE option.workspace_id = outcome.workspace_id
                      AND option.signal_id = outcome.signal_id
                      AND (option.id::text = outcome.option_id
                           OR option.option_id = outcome.option_id)
               ) END AS option_matches
          FROM prediction_outcomes outcome
          JOIN intelligence_signals signal
            ON signal.workspace_id = outcome.workspace_id
           AND signal.signal_id = outcome.signal_id
           AND signal.tenant_id IS NOT DISTINCT FROM outcome.tenant_id
         WHERE outcome.workspace_id = $1
           AND outcome.id::text = $2
           AND (
               $3::text IS NULL
               OR outcome.tenant_id::text = $3
         )
         LIMIT 1
         FOR SHARE OF signal
        """,
        workspace_id,
        str(payload.get("source_id") or "").strip(),
        tenant_id,
    )
    data = dict(row) if row else {}
    if not data:
        raise HTTPException(404, "calibration source not found")
    if not data.get("option_matches") or not observed_signal(data):
        raise HTTPException(404, "calibration source not found")
    actual_value = _number(data.get("actual_value"))
    predicted_value = _number(data.get("signal_predicted_value"))
    metric = str(data.get("metric") or "").strip()
    action = str(data.get("action_taken") or "").strip()
    source_system = str(data.get("source_system") or "").strip()
    if actual_value is None or not metric or not action or not source_system:
        raise HTTPException(409, "authoritative outcome is incomplete")
    horizon = int(data.get("prediction_horizon_days") or 30)
    authoritative = {
        "source_type": "prediction_outcome",
        "source_id": str(data["outcome_id"]),
        "predicted_metric": metric,
        "predicted_probability": None,
        "predicted_value": predicted_value,
        "predicted_interval": {},
        "actual_value": actual_value,
        "actual_status": "unknown",
        "observed_at": _timestamp(data.get("outcome_created_at")),
        "horizon_days": horizon,
        "model_version": calibration.MODEL_VERSION,
        "calibration_group": calibration.source_type_calibration_group(
            source_system, metric
        ),
        "evidence_refs": _verified_refs(data),
        "input_classification": "observed",
    }
    _assert_client_claims(payload, authoritative)
    return authoritative


__all__ = ("resolve_authoritative_observation",)

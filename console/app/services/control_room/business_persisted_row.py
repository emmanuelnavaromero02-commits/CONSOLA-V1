from __future__ import annotations

import json
from collections.abc import Mapping, Set
from typing import Any

from app.services.control_room.business_eligibility import (
    BUSINESS_EVIDENCE_FIELDS,
    BUSINESS_MATERIALIZATION_FIELDS,
    BUSINESS_OBSERVATION_FIELDS,
)
from app.services.control_room.business_observation_codec import (
    nonempty_mapping_fields,
)
from app.services.control_room.business_projection import project_business_item
from app.services.control_room.business_workflow_provenance import (
    workflow_is_quarantined,
)


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key, value in tuple(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _severity(value: Any, weights: Mapping[str, int]) -> str:
    severity = str(value or "medium").strip().lower()
    return severity if severity in weights else "medium"


def persisted_business_item(
    row: Mapping[str, Any],
    *,
    expected_item_id: str,
    item_statuses: Set[str],
    severity_weights: Mapping[str, int],
) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    public = _public(row)
    if str(public.get("item_id") or "") != expected_item_id:
        return None
    metadata = _metadata(public.get("metadata"))
    severity = _severity(public.get("severity"), severity_weights)
    status = str(public.get("status") or "open")
    if status not in item_statuses:
        status = "open"
    intelligence = _metadata(metadata.get("intelligence"))
    decision_intelligence = _metadata(metadata.get("decision_intelligence"))
    if not decision_intelligence:
        decision_intelligence = _metadata(intelligence.get("decision_intelligence"))
    if decision_intelligence:
        intelligence = {**intelligence, "decision_intelligence": decision_intelligence}
    escaped_item_id = expected_item_id.replace("'", "''")
    item = {
        "id": public["item_id"],
        "kind": public.get("item_kind"),
        "item_kind": metadata.get("item_kind") or public.get("item_kind"),
        "tenant_id": public.get("tenant_id") or metadata.get("tenant_id"),
        "workspace_id": public.get("workspace_id") or metadata.get("workspace_id"),
        "owner_user_id": public.get("owner_user_id"),
        "domain": public.get("domain"),
        "module": metadata.get("module") or public.get("cartridge_id"),
        "cartridge": public.get("cartridge_id"),
        "source_dataset": public.get("source_dataset"),
        "source_system": metadata.get("source_system") or public.get("cartridge_id"),
        "dataset": metadata.get("dataset") or public.get("source_dataset"),
        "gold_table": metadata.get("gold_table"),
        "metadata": metadata,
        "entity_kind": public.get("entity_kind") or "Entidad",
        "entity_id": public.get("entity_id") or "",
        "entity_label": public.get("entity_label")
        or public.get("entity_id")
        or public.get("source_dataset")
        or "Entidad",
        "anomaly_type": public.get("anomaly_type") or "control_room_item",
        "severity": severity,
        "severity_weight": severity_weights[severity],
        "detected_at": metadata.get("detected_at") or "",
        "details": _metadata(metadata.get("details")),
        "title": public.get("title"),
        "description": metadata.get("description") or public.get("title"),
        "recommendation": metadata.get("recommendation")
        or "Revisar, decidir y registrar evidencia.",
        "root_cause": metadata.get("root_cause")
        or "Senal persistida en Sala de Control.",
        "impact": metadata.get("impact") or "Riesgo operativo.",
        "sql": metadata.get("sql")
        or f"SELECT * FROM control_room_items WHERE item_id = '{escaped_item_id}'",
        "status": status,
        "decision_id": public.get("decision_id"),
        "impact_estimate": public.get("impact_estimate"),
        "impact_currency": public.get("impact_currency"),
        "confidence": public.get("confidence"),
        "priority_score": public.get("priority_score"),
        "thresholds_applied": metadata.get("thresholds_applied") or [],
        "threshold_state": metadata.get("threshold_state") or "default",
        "control_origin": metadata.get("control_origin"),
        "capabilities": _metadata(metadata.get("capabilities")),
        "math_provenance": _metadata(metadata.get("math_provenance")),
        "monte_carlo": _metadata(metadata.get("monte_carlo")),
        "bayesian_calibration": _metadata(metadata.get("bayesian_calibration")),
        "priority": _metadata(metadata.get("priority")),
        "selected_option_id": public.get("selected_option_id")
        or metadata.get("selected_option_id"),
        "execution_status": public.get("execution_status")
        or metadata.get("execution_status"),
        "alert_state": _metadata(metadata.get("alert_state")),
        "control_state": _metadata(metadata.get("control_state")),
        "lessons": metadata.get("lessons"),
        "learned_rules": metadata.get("learned_rules"),
        "lesson_applications": metadata.get("lesson_applications")
        if isinstance(metadata.get("lesson_applications"), list)
        else [],
        "decision_intelligence": decision_intelligence,
        "intelligence": intelligence,
        "first_seen_at": public.get("first_seen_at"),
        "last_seen_at": public.get("last_seen_at"),
        "resolved_at": public.get("resolved_at"),
        "dismissed_at": public.get("dismissed_at"),
    }
    for key in (
        "freshness_at",
        "freshness_field",
        "data_status",
        "data_readiness",
        "evaluation_status",
        "readiness_status",
        "source_status",
        *BUSINESS_OBSERVATION_FIELDS,
        *BUSINESS_MATERIALIZATION_FIELDS,
        *BUSINESS_EVIDENCE_FIELDS,
        "parent_item_id",
        "source_item_id",
        "derived_from",
    ):
        if key in metadata:
            item[key] = metadata.get(key)
    item.update(nonempty_mapping_fields(metadata, ("lineage",)))
    if workflow_is_quarantined(item):
        if item["status"] in {"decision_created", "approved", "resolved"}:
            item["status"] = "open"
        item.update(
            decision_id=None,
            selected_option_id=None,
            execution_status="not_started",
            decision_intelligence={},
            intelligence={},
        )
    return project_business_item(item)


__all__ = ("persisted_business_item",)

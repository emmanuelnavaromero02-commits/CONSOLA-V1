from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


def _observed_at(signal: Mapping[str, Any]) -> str:
    value = str(signal.get("freshness_at") or signal.get("period_key") or "").strip()
    if len(value) == 10:
        return value + "T00:00:00Z"
    return value


def durable_signal_metadata(
    signal: Mapping[str, Any],
    *,
    tenant_id: str | None,
    workspace_id: str,
) -> dict[str, Any]:
    source_system = str(
        signal.get("source_system") or signal.get("cartridge_id") or ""
    ).strip()
    source_dataset = str(
        signal.get("source_dataset") or signal.get("dataset") or ""
    ).strip()
    observed_at = _observed_at(signal)
    evidence = runtime_row_evidence_fields(
        source_dataset=source_dataset,
        source_system=source_system,
        cartridge=str(signal.get("cartridge_id") or ""),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_row=signal,
        locator_field="signal_id",
        locator_relation="intelligence_signals",
        observed_at=observed_at,
        business_observation={
            "signal_id": signal.get("signal_id"),
            "kind": "signal",
            "metric_name": signal.get("metric"),
            "metric_type": "scalar",
            "observed_value": signal.get("actual_value"),
            "observed_at": observed_at,
        },
    )
    return {
        "metric_name": signal.get("metric_name"),
        "expected_behavior": signal.get("expected_behavior"),
        "signal_subtype": signal.get("signal_subtype") or "observed",
        "source_system": source_system,
        "source_dataset": source_dataset,
        "dataset": signal.get("dataset"),
        "gold_table": signal.get("gold_table") or f"gold_{signal.get('dataset')}",
        "freshness_at": observed_at,
        "freshness_field": signal.get("freshness_field"),
        "evidence_pack_id": signal.get("evidence_pack_id"),
        "decision_intelligence": signal.get("decision_intelligence")
        if isinstance(signal.get("decision_intelligence"), dict)
        else None,
        "intelligence_run_id": signal.get("intelligence_run_id"),
        "run_ref": signal.get("run_ref"),
        "input_classification": "observed",
        "observed": True,
        **evidence,
    }


__all__ = ("durable_signal_metadata",)

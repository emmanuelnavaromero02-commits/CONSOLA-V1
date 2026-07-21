from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_observation_codec import (
    with_observation_envelope,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    persistence_metadata,
)


def canonical_signal_metadata(
    signal: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    item_kind: str,
) -> dict[str, Any]:
    source_dataset = str(
        signal.get("source_dataset") or signal.get("dataset") or ""
    ).strip()
    observed_at = str(
        signal.get("freshness_at") or signal.get("period_key") or ""
    ).strip()
    evidence = runtime_row_evidence_fields(
        source_dataset=source_dataset,
        source_row=signal,
        locator_field="signal_id",
        locator_relation="intelligence_signals",
        observed_at=observed_at,
    )
    observation = {
        "metric_type": "scalar",
        "observed_value": signal.get("actual_value"),
        "observation_date": observed_at,
        "data_status": "gold_ready",
        "source_dataset": source_dataset,
        "source_system": signal.get("source_system") or signal.get("cartridge_id"),
        **evidence,
    }
    enriched = with_observation_envelope({**dict(metadata), **observation}, observation)
    return persistence_metadata(
        {
            "id": signal.get("signal_id"),
            "kind": item_kind,
            "item_kind": item_kind,
            "source_dataset": source_dataset,
            "metadata": enriched,
        }
    )


__all__ = ("canonical_signal_metadata",)

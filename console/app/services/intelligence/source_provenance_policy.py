from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_runtime_evidence import (
    canonical_runtime_row_reference,
)


MANUAL_MARKERS = ("fixture", "manual", "mock", "synthetic")


def metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def manual_marker(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in MANUAL_MARKERS)


def observed_signal(row: Any) -> bool:
    if not row:
        return False
    data = dict(row)
    meta = metadata(data.get("metadata"))
    source_system = data.get("source_system") or meta.get("source_system")
    source_dataset = data.get("source_dataset") or meta.get("source_dataset")
    evidence_pack = data.get("evidence_pack_id") or meta.get("evidence_pack_id")
    return bool(
        data.get("signal_subtype") == "observed"
        and source_system
        and source_dataset
        and evidence_pack
        and not manual_marker(source_system)
        and not manual_marker(source_dataset)
        and not manual_marker(meta.get("source_type"))
        and meta.get("input_classification") != "scenario_assumption"
        and meta.get("observed") is not False
    )


def prediction_outcome_observed(data: dict[str, Any]) -> bool:
    meta = metadata(data.get("metadata"))
    required = (
        data.get("signal_id"),
        data.get("action_taken"),
        data.get("actual_value"),
        data.get("outcome_summary"),
        data.get("owner_user_id"),
        data.get("created_at"),
    )
    if not all(value is not None and str(value).strip() for value in required):
        return False
    if (
        meta.get("source_type") != "prediction_outcome"
        or meta.get("input_classification") != "observed"
        or meta.get("observed") is not True
        or manual_marker(meta.get("source_system"))
        or manual_marker(meta.get("source_dataset"))
    ):
        return False
    refs = meta.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return False
    canonical = [
        reference
        for item in refs
        if isinstance(item, Mapping)
        and (reference := canonical_runtime_row_reference(item)) is not None
    ]
    return any(
        ref.get("source_system") == meta.get("source_system")
        and ref.get("source_dataset") == meta.get("source_dataset")
        for ref in canonical
    )


def backtest_policy(data: dict[str, Any]) -> tuple[str, str | None]:
    labels_available = int(data.get("labels_available") or 0)
    labels_required = int(data.get("labels_required") or 0)
    common = bool(
        data.get("actual_label") is not None
        and data.get("status") == "ok"
        and data.get("completed_at") is not None
        and labels_required > 0
        and labels_available >= labels_required
        and data.get("insufficient_labeled_data") is False
        and data.get("source_system")
        and data.get("source_dataset")
        and not manual_marker(data.get("source_system"))
        and not manual_marker(data.get("source_dataset"))
    )
    if not common:
        return "invalid", None
    result = metadata(data.get("result"))
    if (
        data.get("label_source") == "outcome"
        and data.get("run_mode") == "outcome_linked"
    ):
        outcome_id = str(result.get("outcome_id") or "").strip()
        return ("outcome", outcome_id) if outcome_id else ("invalid", None)
    if (
        data.get("label_source") == "historical_rule"
        and data.get("run_mode") == "historical_replay"
    ):
        rule = str(result.get("label_rule") or "").strip()
        if rule and not manual_marker(rule):
            return "historical", None
    return "invalid", None


__all__ = (
    "backtest_policy",
    "manual_marker",
    "metadata",
    "observed_signal",
    "prediction_outcome_observed",
)

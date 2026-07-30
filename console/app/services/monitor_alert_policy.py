"""Evidence sufficiency boundary for automatic monitor alerts."""

from __future__ import annotations

from typing import Any


_INSUFFICIENT_STATUSES = {
    "blocked",
    "empty",
    "error",
    "insufficient_data",
    "missing",
    "partial",
    "unavailable",
    "unknown",
}


def _signal_count(payload: dict[str, Any]) -> int:
    signals = payload.get("signals")
    if isinstance(signals, list):
        return len(signals)
    if isinstance(signals, dict):
        try:
            return int(signals.get("count") or len(signals.get("items") or []))
        except (TypeError, ValueError):
            return 0
    return 0


def _blockers(payload: dict[str, Any]) -> list[Any]:
    value = payload.get("blockers")
    return value if isinstance(value, list) else []


def _engine_is_incomplete(payload: dict[str, Any]) -> bool:
    evidence = (
        payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    )
    results = (
        evidence.get("engine_results")
        if isinstance(evidence.get("engine_results"), list)
        else []
    )
    return any(
        isinstance(result, dict)
        and str(result.get("status") or "").lower() in {"blocked", "error"}
        for result in results
    )


def monitor_should_alert(contract: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Return true only when evidence is sufficient and a threshold is met."""
    status = str(payload.get("status") or "unknown").strip().lower()
    if payload.get("data_sufficient") is False:
        return False
    if status in _INSUFFICIENT_STATUSES or _engine_is_incomplete(payload):
        return False
    blockers = _blockers(payload)
    signal_count = _signal_count(payload)
    if signal_count <= 0:
        return False
    threshold = (
        contract.get("threshold") if isinstance(contract.get("threshold"), dict) else {}
    )
    status_not_in = threshold.get("status_not_in")
    if isinstance(status_not_in, list) and status not in {
        str(item).lower() for item in status_not_in
    }:
        return True
    if threshold.get("blockers_present") and blockers:
        return True
    try:
        minimum = int(threshold.get("min_signal_count") or 0)
    except (TypeError, ValueError):
        minimum = 0
    if signal_count > 0 and signal_count >= minimum:
        return True
    return False

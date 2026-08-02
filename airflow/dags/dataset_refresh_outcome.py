"""Typed fail-closed HTTP outcomes for the operational refresh chain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _payload(response: Any) -> Mapping[str, Any]:
    if not 200 <= int(getattr(response, "status_code", 0)) < 300:
        raise RuntimeError("operational dependency unavailable")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("operational dependency unavailable") from exc
    if not isinstance(payload, Mapping) or payload.get("error"):
        raise RuntimeError("operational dependency unavailable")
    if "ok" in payload and payload.get("ok") is not True:
        raise RuntimeError("operational dependency unavailable")
    return payload


def require_successful_materialization_response(
    response: Any, *, expected_name: str
) -> dict[str, Any]:
    payload = _payload(response)
    if set(payload) != {"name", "layer", "row_count"}:
        raise RuntimeError("materialization outcome unavailable")
    if str(payload.get("name") or "") != expected_name:
        raise RuntimeError("materialization outcome unavailable")
    if payload.get("layer") not in {"silver", "gold"}:
        raise RuntimeError("materialization outcome unavailable")
    row_count = payload.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise RuntimeError("materialization outcome unavailable")
    return dict(payload)


def require_successful_registry_response(
    response: Any, *, expected_status: str
) -> dict[str, Any]:
    payload = _payload(response)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("pipeline run registry unavailable")
    if result.get("saved") is not True:
        raise RuntimeError("pipeline run registry unavailable")
    if str(result.get("status") or "").lower() != expected_status:
        raise RuntimeError("pipeline run registry unavailable")
    return dict(result)


def require_successful_intelligence_response(response: Any) -> dict[str, Any]:
    payload = _payload(response)
    if payload.get("ok") is not True or payload.get("status") != "completed":
        raise RuntimeError("Gold intelligence trigger unavailable")
    signals = payload.get("signals")
    if isinstance(signals, bool) or not isinstance(signals, int) or signals <= 0:
        raise RuntimeError("Gold intelligence trigger unavailable")
    return dict(payload)


def materialization_status(invocation: object, *, task_state: str) -> str:
    """Classify only complete, typed materialization evidence as successful."""
    if task_state != "success" or not isinstance(invocation, Mapping):
        return "failed"
    if "error" in invocation:
        return "failed"
    results = invocation.get("results")
    completed = invocation.get("materialized")
    if (
        not isinstance(results, list)
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < 0
    ):
        return "failed"
    typed: list[Mapping[str, Any]] = []
    for item in results:
        if not isinstance(item, Mapping):
            return "failed"
        if not isinstance(item.get("ok"), bool):
            return "failed"
        if not str(item.get("name") or "").strip():
            return "failed"
        typed.append(item)
    succeeded = sum(1 for item in typed if item.get("ok") is True)
    if completed != succeeded:
        return "failed"
    reported = invocation.get("status")
    if reported == "no_downstream_datasets":
        return "noop" if completed == 0 and not typed else "failed"
    if reported != "completed" or not typed:
        return "failed"
    if succeeded == len(typed):
        return "success"
    if 0 < succeeded < len(typed):
        return "partial"
    return "failed"

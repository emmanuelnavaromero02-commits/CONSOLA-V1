"""Fail-closed validation for Console Intelligence outcomes."""

from __future__ import annotations

from typing import Any


def _response_body(response: Any) -> dict[str, Any]:
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300:
        raise RuntimeError("downstream outcome unavailable")
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001 - external JSON boundary
        raise RuntimeError("downstream outcome unavailable") from exc
    if not isinstance(body, dict):
        raise RuntimeError("downstream outcome unavailable")
    return body


def require_successful_intelligence_response(response: Any) -> dict[str, Any]:
    """Accept only a complete, typed Intelligence success with a real signal."""
    try:
        body = _response_body(response)
    except RuntimeError as exc:
        raise RuntimeError("intelligence outcome unavailable") from exc
    if body.get("ok") is not True:
        raise RuntimeError("intelligence outcome unavailable")
    if body.get("status") != "completed":
        raise RuntimeError("intelligence outcome unavailable")
    if not isinstance(body.get("run_ref"), str) or not body["run_ref"].strip():
        raise RuntimeError("intelligence outcome unavailable")
    run_id = body.get("intelligence_run_id")
    if not isinstance(run_id, (str, int)) or not str(run_id).strip():
        raise RuntimeError("intelligence outcome unavailable")
    signals = body.get("signals")
    if type(signals) is not int or signals < 1:
        raise RuntimeError("intelligence outcome unavailable")
    return body


def require_successful_materialization_response(
    response: Any, *, expected_name: str
) -> dict[str, Any]:
    """Accept only the exact durable materialization receipt shape."""
    try:
        body = _response_body(response)
    except RuntimeError as exc:
        raise RuntimeError("materialization outcome unavailable") from exc
    if body.get("ok") is False or body.get("error"):
        raise RuntimeError("materialization outcome unavailable")
    if body.get("name") != expected_name or body.get("layer") not in {"silver", "gold"}:
        raise RuntimeError("materialization outcome unavailable")
    if type(body.get("row_count")) is not int or body["row_count"] < 0:
        raise RuntimeError("materialization outcome unavailable")
    uri = body.get("storage_uri")
    if not isinstance(uri, str) or not uri.strip():
        raise RuntimeError("materialization outcome unavailable")
    return body


def require_saved_pipeline_response(
    response: Any, *, expected_run_id: str, expected_status: str
) -> dict[str, Any]:
    try:
        body = _response_body(response)
    except RuntimeError as exc:
        raise RuntimeError("pipeline registry unavailable") from exc
    receipt = body.get("result")
    if (
        not isinstance(receipt, dict)
        or receipt.get("saved") is not True
        or receipt.get("run_id") != expected_run_id
        or receipt.get("status") != expected_status
    ):
        raise RuntimeError("pipeline registry unavailable")
    return receipt


__all__ = [
    "require_successful_intelligence_response",
    "require_successful_materialization_response",
    "require_saved_pipeline_response",
]

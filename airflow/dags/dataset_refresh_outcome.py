from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESULT_OK = "ok"
RESULT_SKIPPED_UPSTREAM_MISSING = "skipped_upstream_missing"
RESULT_FAILED = "failed"

_MISSING_UPSTREAM_ERROR_CODES = {
    "source_files_missing",
    "dependency_not_materialized",
    "missing_materialized_dependencies",
}
_MISSING_UPSTREAM_FALLBACK_REASON = "missing_materialized_dependency"


def missing_upstream_error_code(exc: Any) -> str | None:
    if getattr(exc, "status_code", None) != 409:
        return None
    result = getattr(exc, "result", None)
    detail = result.get("detail") if isinstance(result, Mapping) else None
    code = str(detail.get("code") or "") if isinstance(detail, Mapping) else ""
    return code if code in _MISSING_UPSTREAM_ERROR_CODES else None


def missing_upstream_fallback_reason(payload: Any, *, expected_name: str) -> str | None:
    if not isinstance(payload, Mapping) or payload.get("error"):
        return None
    if "ok" in payload and payload.get("ok") is not True:
        return None
    if payload.get("fallback") is not True:
        return None
    if str(payload.get("fallback_reason") or "") != _MISSING_UPSTREAM_FALLBACK_REASON:
        return None
    if str(payload.get("name") or "") != expected_name:
        return None
    if payload.get("layer") not in {"silver", "gold"}:
        return None
    return _MISSING_UPSTREAM_FALLBACK_REASON


def _payload(response: Any) -> Mapping[str, Any]:
    if not 200 <= int(getattr(response, "status_code", 0)) < 300:
        raise RuntimeError("operational dependency unavailable")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("operational dependency unavailable") from exc
    return _checked(payload)


def _checked(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("error"):
        raise RuntimeError("operational dependency unavailable")
    if "ok" in payload and payload.get("ok") is not True:
        raise RuntimeError("operational dependency unavailable")
    return payload


def require_successful_materialization_response(
    response: Any, *, expected_name: str
) -> dict[str, Any]:
    return require_successful_materialization_payload(
        _payload(response), expected_name=expected_name
    )


def require_successful_materialization_payload(
    payload: Any, *, expected_name: str
) -> dict[str, Any]:
    payload = _checked(payload)
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


def _classified_counts(invocation: object) -> tuple[int, int, int, int] | None:
    if not isinstance(invocation, Mapping) or "error" in invocation:
        return None
    results = invocation.get("results")
    completed = invocation.get("materialized")
    if (
        not isinstance(results, list)
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < 0
    ):
        return None
    succeeded = skipped = failed = 0
    for item in results:
        if not isinstance(item, Mapping):
            return None
        ok = item.get("ok")
        if not isinstance(ok, bool):
            return None
        if not str(item.get("name") or "").strip():
            return None
        classification = item.get("classification")
        if classification is None:
            classification = RESULT_OK if ok else RESULT_FAILED
        if classification == RESULT_OK:
            if ok is not True:
                return None
            succeeded += 1
        elif classification == RESULT_SKIPPED_UPSTREAM_MISSING:
            if ok is not False:
                return None
            skipped += 1
        elif classification == RESULT_FAILED:
            if ok is not False:
                return None
            failed += 1
        else:
            return None
    if completed != succeeded:
        return None
    declared = invocation.get("breakdown")
    if declared is not None:
        if not isinstance(declared, Mapping):
            return None
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in declared.values()
        ):
            return None
        if dict(declared) != {
            RESULT_OK: succeeded,
            RESULT_SKIPPED_UPSTREAM_MISSING: skipped,
            RESULT_FAILED: failed,
        }:
            return None
    return succeeded, skipped, failed, len(results)


def materialization_breakdown(invocation: object) -> dict[str, int] | None:
    counts = _classified_counts(invocation)
    if counts is None:
        return None
    succeeded, skipped, failed, _ = counts
    return {
        RESULT_OK: succeeded,
        RESULT_SKIPPED_UPSTREAM_MISSING: skipped,
        RESULT_FAILED: failed,
    }


def materialization_status(invocation: object, *, task_state: str) -> str:
    if task_state != "success":
        return "failed"
    counts = _classified_counts(invocation)
    if counts is None:
        return "failed"
    succeeded, skipped, failed, total = counts
    reported = invocation.get("status")
    if reported == "no_downstream_datasets":
        return "noop" if total == 0 else "failed"
    if reported != "completed" or total == 0:
        return "failed"
    if failed == 0:
        return "success" if skipped == 0 else "partial"
    return "partial" if succeeded > 0 else "failed"

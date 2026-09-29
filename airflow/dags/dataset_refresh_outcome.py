from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESULT_OK = "ok"
RESULT_DEGRADED = "degraded"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"
RESULT_CLASSES = (RESULT_OK, RESULT_DEGRADED, RESULT_SKIPPED, RESULT_FAILED)

_MISSING_SOURCE_ERROR_CODES = {"source_files_missing", "dependency_not_materialized"}
_FALLBACK_REASON = "missing_materialized_dependency"
PUBLISHED_CLASSES = frozenset({RESULT_OK, RESULT_DEGRADED})


def missing_source_error_code(exc: Any) -> str | None:
    if getattr(exc, "status_code", None) != 409:
        return None
    result = getattr(exc, "result", None)
    detail = result.get("detail") if isinstance(result, Mapping) else None
    code = str(detail.get("code") or "") if isinstance(detail, Mapping) else ""
    return code if code in _MISSING_SOURCE_ERROR_CODES else None


def _row_count(payload: Mapping[str, Any]) -> int:
    row_count = payload.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise RuntimeError("materialization outcome unavailable")
    return row_count


def _fallback_payload(
    payload: Mapping[str, Any], *, expected_name: str
) -> tuple[str, dict[str, Any]]:
    if str(payload.get("fallback_reason") or "") != _FALLBACK_REASON:
        raise RuntimeError("materialization outcome unavailable")
    if str(payload.get("name") or "") != expected_name:
        raise RuntimeError("materialization outcome unavailable")
    if payload.get("layer") not in {"silver", "gold"}:
        raise RuntimeError("materialization outcome unavailable")
    degraded = payload.get("degraded")
    if not isinstance(degraded, bool):
        raise RuntimeError("materialization outcome unavailable")
    row_count = _row_count(payload)
    classification = RESULT_OK if not degraded and row_count > 0 else RESULT_DEGRADED
    return classification, {
        "name": expected_name,
        "layer": payload["layer"],
        "row_count": row_count,
    }


def classify_materialization_payload(
    payload: Any, *, expected_name: str
) -> tuple[str, dict[str, Any]]:
    checked = _checked(payload)
    if checked.get("fallback") is True:
        return _fallback_payload(checked, expected_name=expected_name)
    return RESULT_OK, require_successful_materialization_payload(
        checked, expected_name=expected_name
    )


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
    _row_count(payload)
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


def result_classification(item: Mapping[str, Any]) -> str | None:
    ok = item.get("ok")
    if not isinstance(ok, bool):
        return None
    classification = item.get("classification")
    if classification is None:
        return RESULT_OK if ok else RESULT_FAILED
    if classification not in RESULT_CLASSES:
        return None
    if (classification in PUBLISHED_CLASSES) is not ok:
        return None
    return classification


def _classified_counts(invocation: object) -> tuple[dict[str, int], int] | None:
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
    counts = dict.fromkeys(RESULT_CLASSES, 0)
    for item in results:
        if not isinstance(item, Mapping) or not str(item.get("name") or "").strip():
            return None
        classification = result_classification(item)
        if classification is None:
            return None
        counts[classification] += 1
    if completed != counts[RESULT_OK] + counts[RESULT_DEGRADED]:
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
        if dict(declared) != counts:
            return None
    return counts, len(results)


def materialization_breakdown(invocation: object) -> dict[str, int] | None:
    classified = _classified_counts(invocation)
    return None if classified is None else classified[0]


def materialization_status(invocation: object, *, task_state: str) -> str:
    if task_state != "success":
        return "failed"
    classified = _classified_counts(invocation)
    if classified is None:
        return "failed"
    counts, total = classified
    reported = invocation.get("status")
    if reported == "no_downstream_datasets":
        return "noop" if total == 0 else "failed"
    if reported != "completed" or total == 0:
        return "failed"
    published = counts[RESULT_OK] + counts[RESULT_DEGRADED]
    if counts[RESULT_FAILED]:
        return "partial" if published else "failed"
    if counts[RESULT_OK] == 0 and counts[RESULT_SKIPPED]:
        return "blocked"
    if counts[RESULT_SKIPPED] or counts[RESULT_DEGRADED]:
        return "partial"
    return "success"

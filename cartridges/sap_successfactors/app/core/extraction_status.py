from __future__ import annotations

import re
from typing import Any


_PUBLIC_HTTP_STATUSES = frozenset(
    {400, 401, 403, 404, 408, 409, 412, 429, 500, 502, 503, 504}
)


def safe_http_status(exc: Exception) -> int | None:
    """Return only an allowlisted HTTP status from an exception chain."""

    current: BaseException | None = exc
    for _ in range(4):
        if current is None:
            break
        response = getattr(current, "response", None)
        candidate = getattr(response, "status_code", None) or getattr(
            current, "status_code", None
        )
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            status = None
        if status in _PUBLIC_HTTP_STATUSES:
            return status
        current = current.__cause__ or current.__context__
    match = re.search(
        r"\b(400|401|403|404|408|409|412|429|500|502|503|504)\b", str(exc)
    )
    return int(match.group(1)) if match else None


def classify_successful_extraction(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize successful entity extraction into the live validation vocabulary."""
    if result.get("status") == "skipped_explicit":
        # A metadata guard result is an intentional non-extraction, never an
        # empty-but-valid Bronze publication.
        return dict(result)
    record_count = int(result.get("record_count") or 0)
    if result.get("metadata_status") == "select_pruned" or result.get(
        "metadata_pruned_fields"
    ):
        status = "partial"
        reason = "invalid_select_field"
    else:
        status = "extracted" if record_count > 0 else "empty-valid"
        reason = None
    return {
        **result,
        "status": status,
        **({"reason": reason} if reason else {}),
    }


def classify_extraction_exception(entity: str | None, exc: Exception) -> dict[str, Any]:
    """Classify one failure into a public payload with no exception text."""
    text = str(exc)
    lowered = text.lower()
    code = "FAILED_OPEN"
    status = "failed-open"
    failure_code = "extraction_failed"
    http_status = safe_http_status(exc)

    if lowered in {
        "storage_credentials_missing",
        "storage_signature_invalid",
        "storage_access_denied",
        "storage_bucket_missing",
    }:
        code = lowered
        status = "blocked"
        failure_code = lowered
    elif (
        "config_incomplete" in lowered
        or "requires entity_config.connection_id" in lowered
        or "no default connection fallback" in lowered
    ):
        code = "CONFIG_INCOMPLETE"
        status = "auth-blocked"
        failure_code = "configuration_incomplete"
    elif (
        "oauth/token" in lowered
        or "saml bearer token request failed" in lowered
        or "oauth token request failed" in lowered
    ):
        code = "AUTH_BLOCKED"
        status = "auth-blocked"
        failure_code = "successfactors_auth_failed"
    elif "401" in lowered or "403" in lowered:
        code = "SUCCESSFACTORS_PERMISSION"
        status = "permission-blocked"
        failure_code = "successfactors_access_denied"
    elif (
        "successfactors" in lowered or "/odata/v2/" in lowered or "odata" in lowered
    ) and any(
        marker in lowered
        for marker in (
            "http 400",
            "http 404",
            "400 client error",
            "404 client error",
            "bad request",
            "notfoundexception",
            " is not found",
            "invalid property",
            "invalid query option",
        )
    ):
        code = "SUCCESSFACTORS_METADATA_BLOCKED"
        status = "permission-blocked"
        failure_code = "successfactors_metadata_invalid"

    return {
        "entity": entity,
        "status": status,
        "code": code,
        "failure_code": failure_code,
        **({"http_status": http_status} if http_status is not None else {}),
    }


def public_failure_message(classified: dict[str, Any]) -> str:
    """Stable message suitable for pipeline rows, XCom and API responses."""

    return str(classified.get("failure_code") or "extraction_failed")


_METADATA_BLOCKED_CODE = "SUCCESSFACTORS_METADATA_BLOCKED"
_METADATA_UPSTREAM_CODES = frozenset(
    {"metadata_upstream_unavailable", "metadata_rate_limited"}
)
_METADATA_ACCESS_CODES = frozenset({"metadata_access_denied"})


def _metadata_failure_codes(outcomes: list[dict[str, Any]]) -> set[str]:
    return {
        str(outcome.get("failure_code") or "")
        for outcome in outcomes
        if isinstance(outcome, dict)
        and outcome.get("code") == _METADATA_BLOCKED_CODE
        and outcome.get("failure_code")
    }


def hard_failure_code(
    summary: dict[str, int],
    results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    attempted: int,
) -> str | None:
    """Why a whole extract_all run must be recorded as failed, or None.

    Per entity the classification is already honest: a 401 is
    ``permission-blocked``, a timeout is ``failed-open``. The aggregate was
    not: since 9099d16b (2026-06-26) it was a constant ``partial`` for anything
    short of a clean run, so "every entity died on the network" and "one
    entity had a pruned select" produced the same row, the same green Airflow
    task and the same Panel. The rule here is deliberately narrow: a run is
    failed only when it produced nothing at all, and then by the reason it
    produced nothing.

    * ``extraction_failed``: an entity failed open (network, storage, an
      unclassified exception) and nothing was extracted.
    * ``configuration_incomplete``: the connection itself is not configured.
    * ``successfactors_metadata_unavailable``: the ``$metadata`` preflight was
      unreachable or throttled, in the plan or for every attempted entity.
    * ``successfactors_access_denied``: credentials were rejected, by the
      preflight or by every attempted entity.

    Everything else stays partial, including a run where most entities
    extracted and one timed out: gold was refreshed with what arrived and the
    intelligence cascade must still run. A tenant that lacks permission for
    some entities, a pruned select and a 404 on one EntitySet stay partial too.
    """
    produced = sum(
        int(summary.get(key) or 0) for key in ("extracted", "empty_valid", "partial")
    )
    if produced:
        return None
    if int(summary.get("failed_open") or 0) > 0:
        return "extraction_failed"
    if any(
        isinstance(outcome, dict)
        and (
            outcome.get("failure_code") == "configuration_incomplete"
            or outcome.get("code") == "CONFIGURATION_INCOMPLETE"
        )
        for outcome in results
    ):
        return "configuration_incomplete"
    # The plan's preflight lands in ``skipped``; the per-entity guard lands in
    # ``results`` as ``skipped_explicit``. Both carry the same codes.
    codes = _metadata_failure_codes(results) | _metadata_failure_codes(skipped)
    if codes & _METADATA_ACCESS_CODES:
        return "successfactors_access_denied"
    if codes & _METADATA_UPSTREAM_CODES:
        return "successfactors_metadata_unavailable"
    if attempted > 0:
        rejected = int(summary.get("auth_blocked") or 0) + int(
            summary.get("permission_blocked") or 0
        )
        if rejected >= attempted:
            return "successfactors_access_denied"
    return None


def summarize_extraction_results(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "extracted": 0,
        "empty_valid": 0,
        "partial": 0,
        "blocked": 0,
        "skipped_explicit": 0,
        "auth_blocked": 0,
        "permission_blocked": 0,
        "failed_open": 0,
    }
    for result in results:
        status = result.get("status")
        if status == "extracted":
            counts["extracted"] += 1
        elif status == "empty-valid":
            counts["empty_valid"] += 1
        elif status == "partial":
            counts["partial"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        elif status == "skipped_explicit":
            counts["skipped_explicit"] += 1
        elif status == "auth-blocked":
            counts["auth_blocked"] += 1
        elif status == "permission-blocked":
            counts["permission_blocked"] += 1
        elif status == "failed-open":
            counts["failed_open"] += 1
    return counts

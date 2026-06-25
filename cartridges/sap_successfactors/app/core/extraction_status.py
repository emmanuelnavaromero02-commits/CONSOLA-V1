from __future__ import annotations

from typing import Any


def classify_successful_extraction(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize successful entity extraction into the live validation vocabulary."""
    record_count = int(result.get("record_count") or 0)
    status = "extracted" if record_count > 0 else "empty-valid"
    return {**result, "status": status}


def classify_extraction_exception(entity: str | None, exc: Exception) -> dict[str, Any]:
    """Classify one entity failure without hiding the rest of the batch."""
    text = str(exc)
    lowered = text.lower()
    code = "FAILED_OPEN"
    status = "failed-open"

    if (
        "config_incomplete" in lowered
        or "requires entity_config.connection_id" in lowered
        or "no default connection fallback" in lowered
    ):
        code = "CONFIG_INCOMPLETE"
        status = "auth-blocked"
    elif "oauth/token" in lowered or "saml bearer token request failed" in lowered or "oauth token request failed" in lowered:
        code = "AUTH_BLOCKED"
        status = "auth-blocked"
    elif "401" in lowered or "403" in lowered:
        code = "SUCCESSFACTORS_PERMISSION"
        status = "permission-blocked"
    elif (
        ("successfactors" in lowered or "/odata/v2/" in lowered or "odata" in lowered)
        and any(
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
        )
    ):
        code = "SUCCESSFACTORS_METADATA_BLOCKED"
        status = "permission-blocked"

    return {
        "entity": entity,
        "status": status,
        "code": code,
        "error": text,
    }


def summarize_extraction_results(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "extracted": 0,
        "empty_valid": 0,
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
        elif status == "auth-blocked":
            counts["auth_blocked"] += 1
        elif status == "permission-blocked":
            counts["permission_blocked"] += 1
        elif status == "failed-open":
            counts["failed_open"] += 1
    return counts

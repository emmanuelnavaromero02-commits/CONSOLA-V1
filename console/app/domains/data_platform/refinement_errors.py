from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.logging_config import _redact


def upstream_error_detail(response: Any, fallback: str = "Upstream request failed") -> Any:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or fallback
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, dict):
            return detail
        if detail:
            return str(detail)
        result = payload.get("result")
        if isinstance(result, dict):
            nested = (
                result.get("detail") or result.get("error") or result.get("message")
            )
            if isinstance(nested, dict):
                return nested
            if nested:
                return str(nested)
    return fallback


def payload_error_detail(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("detail", "error", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = payload_error_detail(value)
            if nested:
                return nested
            return _redact(str(value))
        if value:
            detail = str(value)
            raw_error = payload.get("raw_error")
            if raw_error and str(raw_error) not in detail:
                detail = f"{detail}: {raw_error}"
            code = payload.get("code")
            if code and str(code) not in detail:
                detail = f"{code}: {detail}"
            return _redact(detail) or detail
    result = payload.get("result")
    if isinstance(result, dict):
        return payload_error_detail(result)
    return None


def refinement_error_status(detail: str) -> int:
    lower = (detail or "").lower()
    if any(
        token in lower
        for token in (
            "accessdenied",
            "access denied",
            "not authorized",
            "forbidden",
            "permission",
            "outside the caller tenant",
            "unapproved bucket",
            "storage path not allowed",
            "path not allowed",
        )
    ):
        return 403
    if any(
        token in lower
        for token in (
            "source_files_missing",
            "no files found",
            "not found",
            "404",
            "no such key",
            "does not exist",
        )
    ):
        return 404
    if any(
        token in lower
        for token in (
            "timeout",
            "timed out",
            "connecterror",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
        )
    ):
        return 503
    if any(
        token in lower
        for token in (
            "sql could not be parsed",
            "failed ast parse",
            "invalid bronze source",
            "sql is required",
        )
    ):
        return 400
    return 502


def raise_for_refinement_payload_error(
    payload: Any, fallback: str = "Refinement request failed"
) -> None:
    detail = payload_error_detail(payload)
    if not detail:
        return
    raise HTTPException(refinement_error_status(detail), detail or fallback)

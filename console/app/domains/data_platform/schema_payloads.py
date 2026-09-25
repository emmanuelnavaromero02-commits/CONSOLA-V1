from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from app.logging_config import _redact


def _column_name(item: Any) -> str | None:
    if isinstance(item, dict):
        raw = item.get("name") or item.get("column_name") or item.get("column") or item.get("field")
    else:
        raw = item
    name = str(raw or "").strip()
    return name or None


def normalize_preview_payload(source: str, preview: Any) -> dict[str, Any]:
    if not isinstance(preview, dict):
        return empty_preview(source, "partial", "datos parciales")
    payload = dict(preview)
    rows = payload.get("rows") or payload.get("data") or payload.get("result") or []
    if not isinstance(rows, list):
        rows = []
    raw_schema = payload.get("schema") or payload.get("columns") or payload.get("fields") or []
    if not isinstance(raw_schema, list):
        raw_schema = []
    schema: list[dict[str, Any]] = []
    for item in raw_schema:
        if isinstance(item, dict):
            name = _column_name(item)
            schema.append({**item, "name": name} if name else dict(item))
        else:
            name = _column_name(item)
            if name:
                schema.append({"name": name})
    if not schema and rows and isinstance(rows[0], dict):
        schema = [{"name": str(key)} for key in rows[0].keys()]
    payload["schema"] = schema
    payload["columns"] = schema
    payload["rows"] = rows
    payload["data"] = rows
    return payload


def empty_partitions(
    source: str, status: str | None = None, message: str | None = None
) -> dict:
    payload: dict[str, Any] = {
        "source": source,
        "partitions": [],
        "latest": None,
        "sql_latest": None,
    }
    if status:
        payload["status"] = status
    if message:
        payload["message"] = message
    return payload


def empty_preview(
    source: str, status: str | None = None, message: str | None = None
) -> dict:
    payload: dict[str, Any] = {
        "source": source,
        "schema": [],
        "columns": [],
        "rows": [],
        "data": [],
    }
    if status:
        payload["status"] = status
    if message:
        payload["message"] = message
    return payload


def schema_error(stage: str, exc: Exception) -> dict:
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        detail = str(exc.detail or "error leyendo fuente")
    else:
        status_code = 502
        detail = str(exc) or type(exc).__name__
    lower = detail.lower()
    if status_code == 404 or any(
        token in lower
        for token in (
            "source_files_missing",
            "no files found",
            "not found",
            "no such key",
            "does not exist",
        )
    ):
        message = "sin parquet materializado"
        reason = "source_files_missing"
    elif status_code == 403 or any(
        token in lower
        for token in ("permission", "forbidden", "access denied", "not authorized")
    ):
        message = "sin permisos para leer la fuente"
        reason = "permission_denied"
    else:
        message = "error leyendo fuente"
        reason = "read_error"
    return {
        "stage": stage,
        "status_code": status_code,
        "reason": reason,
        "message": message,
        "detail": detail,
    }


def schema_payload_warnings(stage: str, payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("warnings") or []
    if isinstance(warnings, dict):
        warnings = [warnings]
    if not isinstance(warnings, list):
        warnings = [warnings]
    normalized: list[dict] = []
    for warning in warnings:
        if isinstance(warning, dict):
            detail = str(
                warning.get("detail")
                or warning.get("warning")
                or warning.get("reason")
                or "datos parciales"
            )
            reason = str(warning.get("reason") or "partial")
            message = str(warning.get("message") or "datos parciales")
            warning_stage = str(warning.get("stage") or stage)
        else:
            detail = str(warning or "datos parciales")
            reason = "partial"
            message = "datos parciales"
            warning_stage = stage
        normalized.append(
            {
                "stage": warning_stage,
                "status_code": 200,
                "reason": reason,
                "message": message,
                "detail": _redact(detail) or detail,
            }
        )
    if str(payload.get("status") or "").lower() == "partial" and not normalized:
        normalized.append(
            {
                "stage": stage,
                "status_code": 200,
                "reason": "partial",
                "message": "datos parciales",
                "detail": "La fuente devolvió un resultado parcial.",
            }
        )
    return normalized


def preview_has_columns(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("columns", "schema", "fields"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    rows = payload.get("rows") or payload.get("data") or payload.get("result")
    return isinstance(rows, list) and bool(rows)


def schema_status(errors: list[dict], preview: dict) -> str:
    if not errors:
        return "ready"
    hard_stages = {
        str(error.get("stage") or "")
        for error in errors
        if int(error.get("status_code") or 0) >= 400
    }
    if {"partitions", "preview"}.issubset(hard_stages):
        return "error"
    if "preview" in hard_stages and not preview_has_columns(preview):
        return "error"
    return "partial"


def schema_message(status: str, errors: list[dict]) -> str | None:
    if not errors:
        return None
    if status == "error":
        hard_messages = [
            str(error.get("message") or "")
            for error in errors
            if int(error.get("status_code") or 0) >= 400
        ]
        if hard_messages and all(message == hard_messages[0] for message in hard_messages):
            return hard_messages[0]
        return hard_messages[0] if hard_messages else "error leyendo fuente"
    if any(error.get("reason") == "empty_schema" for error in errors):
        return "sin columnas inferidas"
    return "datos parciales"


def gold_schema_error_payload(source: str, error: dict) -> dict:
    message = str(error.get("message") or "error leyendo fuente")
    return {
        "source": source,
        "source_kind": "gold",
        "status": "error",
        "message": message,
        "errors": [error],
        "partitions": empty_partitions(source, "error", message),
        "preview": empty_preview(source, "error", message),
    }


def bronze_schema_payload(
    source: str,
    partitions: dict,
    preview: dict,
    errors: list[dict],
) -> dict:
    preview = normalize_preview_payload(source, preview)
    status = schema_status(errors, preview)
    message = schema_message(status, errors)
    payload: dict[str, Any] = {
        "source": source,
        "source_kind": "bronze",
        "status": status,
        "partitions": partitions,
        "preview": preview,
    }
    if message:
        payload["message"] = message
    if errors:
        payload["errors"] = errors
    return payload


def dataset_detail_columns(schema_payload: dict | None) -> list[dict]:
    if not isinstance(schema_payload, dict):
        return []
    raw = (
        schema_payload.get("columns")
        or schema_payload.get("fields")
        or schema_payload.get("schema")
        or []
    )
    if not isinstance(raw, list):
        return []
    columns = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name") or item.get("column") or item.get("column_name")
            columns.append({**item, "name": name} if name else dict(item))
        elif item:
            columns.append({"name": str(item)})
    return columns


def normalize_dataset_detail(
    definition: dict,
    schema_payload: dict | None = None,
    schema_error: str | None = None,
    user: dict | None = None,
    sanitize_dataset_metadata: Callable[[dict | None, dict], dict] | None = None,
) -> dict:
    if sanitize_dataset_metadata:
        definition = sanitize_dataset_metadata(user, definition)
    row_count = definition.get("row_count")
    status = str(definition.get("status") or "").strip().lower()
    if schema_error:
        status = "unavailable"
    elif not status:
        status = "empty" if row_count == 0 else "ok"
    sql = definition.get("sql") or definition.get("sql_def") or ""
    metadata = (
        definition.get("metadata")
        if isinstance(definition.get("metadata"), dict)
        else {}
    )
    metadata = {
        **metadata,
        "sources": definition.get("sources") or [],
        "schedule": definition.get("schedule"),
        "description": definition.get("description") or "",
        "column_mapping": definition.get("column_mapping") or {},
    }
    return {
        "name": definition.get("name"),
        "layer": definition.get("layer"),
        "type": definition.get("type") or definition.get("layer"),
        "sql": sql,
        "sql_def": sql,
        "columns": dataset_detail_columns(schema_payload),
        "metadata": metadata,
        "source_load_date": definition.get("source_load_date"),
        "source_batch_id": definition.get("source_batch_id"),
        "status": status,
        "error": schema_error or definition.get("error"),
        "row_count": row_count,
        "cartridge": definition.get("cartridge"),
        "updated_at": definition.get("updated_at") or definition.get("last_refresh"),
        "last_refresh": definition.get("last_refresh"),
        "sources": definition.get("sources") or [],
        "column_mapping": definition.get("column_mapping") or {},
        "is_stale": definition.get("is_stale"),
        "staleness_reason": definition.get("staleness_reason"),
    }

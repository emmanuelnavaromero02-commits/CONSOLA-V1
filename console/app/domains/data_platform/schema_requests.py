"""Schema request orchestration helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def schema_response_payload(
    *,
    source: str,
    user: dict | None,
    gold_dataset_from_source: Callable[[str], str | None],
    gold_schema_payload: Callable[[str, dict | None], Awaitable[dict[str, Any]]],
    refinement_invoke: Callable[..., Awaitable[dict[str, Any]]],
    schema_error: Callable[[str, Exception], dict[str, Any]],
    gold_schema_error_payload: Callable[[str, dict[str, Any]], dict[str, Any]],
    empty_partitions: Callable[[str, str | None, str | None], dict[str, Any]],
    empty_preview: Callable[[str, str | None, str | None], dict[str, Any]],
    schema_payload_warnings: Callable[[str, Any], list[dict[str, Any]]],
    preview_has_columns: Callable[[Any], bool],
    bronze_schema_payload: Callable[
        [str, dict[str, Any], dict[str, Any], list[dict[str, Any]]],
        dict[str, Any],
    ],
) -> dict[str, Any]:
    if gold_dataset_from_source(source):
        try:
            return await gold_schema_payload(source, user)
        except Exception as exc:
            error = schema_error("gold", exc)
            return gold_schema_error_payload(source, error)

    errors: list[dict[str, Any]] = []
    try:
        partitions = await refinement_invoke(
            "get_source_partitions",
            {"source": source},
            timeout=30,
            user=user,
        )
    except Exception as exc:
        error = schema_error("partitions", exc)
        errors.append(error)
        partitions = empty_partitions(source, "error", error["message"])

    try:
        preview = await refinement_invoke(
            "preview_source",
            {"source": source, "limit": 5},
            timeout=30,
            user=user,
        )
    except Exception as exc:
        error = schema_error("preview", exc)
        errors.append(error)
        preview = empty_preview(source, "error", error["message"])

    errors.extend(schema_payload_warnings("partitions", partitions))
    errors.extend(schema_payload_warnings("preview", preview))
    if not preview_has_columns(preview):
        errors.append(
            {
                "stage": "preview",
                "status_code": 200,
                "reason": "empty_schema",
                "message": "sin columnas inferidas",
                "detail": "La fuente no devolvió columnas inferidas desde el parquet.",
            }
        )

    return bronze_schema_payload(source, partitions, preview, errors)

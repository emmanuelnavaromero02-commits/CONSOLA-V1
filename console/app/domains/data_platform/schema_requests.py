"""Schema request orchestration helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def sources_response_payload(
    *,
    user: dict | None,
    refinement_invoke: Callable[..., Awaitable[dict[str, Any]]],
    gold_sources_from_catalog: Callable[[dict | None], Awaitable[list[str]]],
    filter_technical_sources: Callable[[dict | None, list[Any]], list[Any]],
) -> dict[str, list[str]]:
    try:
        data = await refinement_invoke("list_sources", {}, timeout=60, user=user)
    except Exception:
        data = {}
    sources = data.get("result") or data.get("sources") or []
    gold_sources = await gold_sources_from_catalog(user)
    if isinstance(sources, list):
        visible = filter_technical_sources(user, sources)
        return {
            "sources": sorted(
                set([str(s) for s in visible if str(s).strip()] + gold_sources)
            )
        }
    return {"sources": gold_sources}


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
    storage_schema_fallback: Callable[[str], Awaitable[dict[str, Any] | None]]
    | None = None,
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
    if not preview_has_columns(preview) and storage_schema_fallback is not None:
        try:
            fallback_preview = await storage_schema_fallback(source)
        except Exception as exc:
            error = schema_error("preview_storage_uri", exc)
            errors.append(error)
            fallback_preview = None
        if fallback_preview and preview_has_columns(fallback_preview):
            preview = fallback_preview
            errors.append(
                {
                    "stage": "preview_storage_uri",
                    "status_code": 200,
                    "reason": "storage_uri_schema_fallback",
                    "message": "schema inferido desde parquet registrado",
                    "detail": (
                        "El preview original no devolvió columnas; se usó el "
                        "parquet exacto registrado para esta ejecución."
                    ),
                }
            )

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

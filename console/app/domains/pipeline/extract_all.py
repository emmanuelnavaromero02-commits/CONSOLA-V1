from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


async def fanout_pipeline_extract_all(
    *,
    cartridge: str,
    body: dict[str, Any],
    user: dict | None,
    api_pipeline: Callable[..., Awaitable[dict[str, Any]]],
    api_pipeline_extract: Callable[..., Awaitable[dict[str, Any]]],
    call_with_optional_user: Callable[..., Awaitable[Any]],
    sync_entity_idempotency_key: Callable[[Any, str], str | None],
    error_id_factory: Callable[[], str],
    logger_exception: Callable[..., None] | None = None,
) -> dict[str, Any]:
    pipeline = await call_with_optional_user(api_pipeline, cartridge, user=user)
    rows = pipeline.get("pipeline") or []
    triggered: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for row in rows:
        entity = row.get("entity")
        if not entity:
            continue
        extract_body = dict(body)
        scoped_idempotency_key = sync_entity_idempotency_key(
            body.get("idempotency_key") or body.get("request_id"),
            entity,
        )
        if scoped_idempotency_key:
            extract_body["idempotency_key"] = scoped_idempotency_key
        try:
            result = await call_with_optional_user(
                api_pipeline_extract, cartridge, entity, extract_body, user=user
            )
            triggered.append(
                {
                    "entity": entity,
                    "job_id": result.get("job_id")
                    or result.get("dag_run_id")
                    or result.get("run_id"),
                    "dag_run_id": result.get("dag_run_id") or result.get("run_id"),
                    "dag_id": result.get("dag_id"),
                    "state": result.get("state") or result.get("status"),
                    "result": result,
                }
            )
        except HTTPException as exc:
            errors.append(
                {
                    "entity": entity,
                    "status_code": exc.status_code,
                    "error": str(exc.detail),
                }
            )
        except Exception:
            error_id = error_id_factory()
            if logger_exception is not None:
                logger_exception(
                    "pipeline extract_all failed for %s.%s error_id=%s",
                    cartridge,
                    entity,
                    error_id,
                )
            errors.append(
                {
                    "entity": entity,
                    "status_code": 500,
                    "error": f"Internal server error. error_id={error_id}",
                }
            )

    blocked = [
        item for item in errors if item.get("status_code") in {400, 403, 404}
    ]
    failed = [
        item for item in errors if item.get("status_code") not in {400, 403, 404}
    ]
    return {
        "cartridge": cartridge,
        "attempted": len(triggered) + len(errors),
        "triggered": triggered,
        "errors": errors,
        "blocked": blocked,
        "failed": failed,
        "partial": [],
        "skipped_explicit": [],
        "summary": {
            "triggered": len(triggered),
            "errors": len(errors),
            "blocked": len(blocked),
            "failed": len(failed),
        },
        "count": len(triggered),
        "error_count": len(errors),
    }

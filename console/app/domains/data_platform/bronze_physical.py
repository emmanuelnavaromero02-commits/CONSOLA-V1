from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


async def count_bronze_parquet_rows(
    *,
    source: str,
    latest_date: str,
    user: dict | None,
    bronze_latest_s3_glob: Callable[[str, str, dict | None], str],
    hdr_for: Callable[[str], dict[str, str]],
    mcp_payload: Callable[..., dict[str, Any]],
    refinement_url: str,
) -> int | None:
    parquet_glob = bronze_latest_s3_glob(source, latest_date, user)
    sql = (
        "SELECT COUNT(*) AS record_count "
        f"FROM read_parquet('{parquet_glob}', hive_partitioning=true, union_by_name=true)"
    )
    async with httpx.AsyncClient(
        headers=hdr_for("REFINEMENT"),
        timeout=60,
    ) as c:
        r = await c.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload(
                "preview_transform", {"sql": sql, "limit": 1, "sources": [source]}, user
            ),
        )
    if r.status_code != 200:
        return None
    data = r.json()
    rows = data.get("data") or []
    if not rows:
        return None
    row = rows[0]
    value = row.get("record_count")
    if value is None and row:
        value = next(iter(row.values()))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def bronze_physical_snapshot(
    *,
    cartridge: str,
    entity: str,
    user: dict | None,
    safe_pipeline_name: Callable[[str], bool],
    build_security_context: Callable[[dict | None], dict[str, Any]],
    explorer_path_allowed: Callable[..., bool],
    minio_client: Callable[[], Any],
    bronze_latest_date_from_objects: Callable[[str, str, list[str]], str | None],
    count_rows: Callable[[str, str, dict | None], Awaitable[int | None]],
) -> dict:
    if not safe_pipeline_name(cartridge) or not safe_pipeline_name(entity):
        return {}

    source = f"raw/{cartridge}/{entity}"
    ctx = build_security_context(user)
    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    list_prefix = f"{source}/"
    if tenant and workspace:
        list_prefix = f"{source}/tenant_id={tenant}/workspace_id={workspace}/"
    if not explorer_path_allowed(list_prefix, user):
        return {}
    bucket = os.environ.get("MINIO_BUCKET", "lakehouse")
    try:

        def _list_object_names() -> list[str]:
            client = minio_client()
            return [
                obj.object_name
                for obj in client.list_objects(
                    bucket, prefix=list_prefix, recursive=True
                )
            ]

        object_names = await asyncio.to_thread(_list_object_names)
        latest_date = bronze_latest_date_from_objects(cartridge, entity, object_names)
        if not latest_date:
            return {}
        return {
            "latest_date": latest_date,
            "record_count": await count_rows(source, latest_date, user),
        }
    except Exception:
        return {}

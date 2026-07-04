"""Bronze query request helpers."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException


async def bronze_query_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    merge_sources: Any,
    rewrite_paths: Any,
    rls_user_context: Any,
    headers_factory: Any,
    mcp_payload: Any,
    refinement_url: str,
    upstream_error_detail: Any,
    raise_for_refinement_payload_error: Any,
    http_client_factory: Any = httpx.AsyncClient,
) -> dict[str, Any]:
    sql = body.get("sql", "").strip()
    limit = min(int(body.get("limit", 200)), 2000)
    if not sql:
        raise HTTPException(400, "sql is required")

    sources = merge_sources(body.get("sources"), sql)
    sql = rewrite_paths(sql, user)
    async with http_client_factory(
        headers=headers_factory("REFINEMENT"),
        timeout=120,
    ) as client:
        response = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload(
                "preview_transform",
                {
                    "sql": sql,
                    "limit": limit,
                    "sources": sources,
                    "user_context": rls_user_context(user),
                },
                user,
            ),
        )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            upstream_error_detail(response, "Refinement query failed"),
        )
    result = response.json()
    raise_for_refinement_payload_error(result, "Refinement query failed")
    return result

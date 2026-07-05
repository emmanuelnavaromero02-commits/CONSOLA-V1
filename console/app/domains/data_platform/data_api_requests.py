from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from app.domains.data_platform.data_api_payloads import (
    DataApiQueryValidationError,
    data_api_columns_param,
    data_api_filtered_query,
    data_api_invalid_column,
    data_api_options_response,
    data_api_options_sql,
    data_api_query_limit,
    data_api_valid_dataset_name,
)


def _validate_dataset_name(dataset: str) -> None:
    if not data_api_valid_dataset_name(dataset):
        raise HTTPException(400, "Invalid dataset name")


async def data_options_payload(
    *,
    dataset: str,
    columns: str,
    user: dict[str, Any],
    refinement_url: str,
    http_client_factory: Any,
    headers_factory: Any,
    mcp_payload_factory: Any,
    rls_user_context: Any,
    upstream_error_detail: Any,
) -> dict[str, list[str]] | list[Any]:
    _validate_dataset_name(dataset)
    cols = data_api_columns_param(columns)
    if not cols:
        raise HTTPException(
            400, "columns param required, e.g. ?columns=revenue_manager,cliente"
        )

    invalid_col = data_api_invalid_column(cols)
    if invalid_col:
        raise HTTPException(400, f"Invalid column name: {invalid_col}")

    union_sql = data_api_options_sql(dataset, cols)
    try:
        async with http_client_factory(
            headers=headers_factory("REFINEMENT"), timeout=30
        ) as client:
            response = await client.post(
                f"{refinement_url}/mcp/invoke",
                json=mcp_payload_factory(
                    "preview_transform",
                    {
                        "sql": union_sql,
                        "limit": 5000,
                        "user_context": rls_user_context(user),
                    },
                    user,
                ),
            )
    except httpx.TransportError as exc:
        raise HTTPException(500, "Options backend unavailable") from exc
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            upstream_error_detail(response, "Options backend failed"),
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise HTTPException(500, "Options backend returned invalid JSON") from exc
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(500, "Options backend failed")
    if not isinstance(result, dict):
        raise HTTPException(500, "Options backend returned invalid payload")
    return data_api_options_response(cols, result.get("data", []))


async def filtered_data_query_payload(
    *,
    dataset: str,
    body: dict[str, Any],
    user: dict[str, Any],
    refinement_url: str,
    http_client_factory: Any,
    headers_factory: Any,
    mcp_payload_factory: Any,
    rls_user_context: Any,
) -> list[Any]:
    _validate_dataset_name(dataset)
    filters = body.get("filters", {})
    limit = data_api_query_limit(body.get("limit", 2000))
    columns = body.get("columns", ["*"])
    user_context = rls_user_context(user)

    try:
        filtered_query = data_api_filtered_query(
            dataset,
            filters=filters,
            limit=limit,
            columns=columns,
        )
    except DataApiQueryValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    async with http_client_factory(
        headers=headers_factory("REFINEMENT"), timeout=60
    ) as client:
        response = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload_factory(
                "preview_transform",
                {
                    "sql": filtered_query.sql,
                    "params": filtered_query.params,
                    "limit": filtered_query.limit,
                    "user_context": user_context,
                },
                user,
            ),
        )
    if response.status_code != 200:
        raise HTTPException(response.status_code, "Query failed")
    result = response.json()
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result.get("data", [])

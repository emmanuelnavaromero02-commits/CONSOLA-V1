"""Copilot LLM key payload helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException


def llm_secret_keys(data: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in data.get("keys") or []:
        if key:
            keys.add(str(key))
    for item in data.get("secrets") or []:
        if isinstance(item, dict) and item.get("key"):
            keys.add(str(item["key"]))
    return keys


async def llm_key_status_payload(
    *,
    vault_scope: str,
    vault_url: str,
    vault_headers: dict[str, str],
    http_client_factory: Any = httpx.AsyncClient,
) -> dict[str, Any]:
    try:
        async with http_client_factory(headers=vault_headers, timeout=5) as client:
            response = await client.get(
                f"{vault_url}/secrets/{quote(vault_scope, safe='')}"
            )
        if response.status_code in {404, 204}:
            return {"provider": "anthropic", "configured": False, "scope": "llm"}
        response.raise_for_status()
        data = response.json() if response.content else {}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Vault request failed") from exc

    return {
        "provider": "anthropic",
        "configured": "anthropic_api_key"
        in llm_secret_keys(data if isinstance(data, dict) else {}),
        "scope": "llm",
    }


async def llm_key_set_payload(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    vault_scope: str,
    vault_url: str,
    vault_headers: dict[str, str],
    audit_record_event: Any,
    http_client_factory: Any = httpx.AsyncClient,
) -> dict[str, Any]:
    value = str(body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "value is required")

    try:
        async with http_client_factory(headers=vault_headers, timeout=5) as client:
            response = await client.put(
                f"{vault_url}/secrets/{quote(vault_scope, safe='')}/anthropic_api_key",
                json={"value": value},
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Vault request failed") from exc

    await audit_record_event(
        user.get("id"),
        user.get("email"),
        "copilot.llm_key.upsert",
        "vault_secret",
        "llm/anthropic_api_key",
        status="success",
        metadata={
            "provider": "anthropic",
            "scope": "llm",
            "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
            "workspace_id": user.get("active_workspace_id")
            or user.get("workspace_id"),
        },
        critical=True,
    )
    return {"provider": "anthropic", "configured": True, "scope": "llm"}

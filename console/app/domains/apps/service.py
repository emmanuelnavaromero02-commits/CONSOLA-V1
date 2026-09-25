from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager

from fastapi import HTTPException

from app.domains.apps.payloads import (
    app_payload_cartridge_candidates,
    filter_apps_payload_to_ready_datasets,
    filter_apps_payload_to_scoped_connections,
)


LoadAppsPayload = Callable[[dict], Awaitable[Any]]
RequireCartridgeVisible = Callable[[dict, str], None]
ResolveCartridges = Callable[[dict | None, set[str]], Awaitable[set[str]]]
GoldReadiness = Callable[[dict | None, Any], Awaitable[tuple[set[str] | None, str]]]
HttpClientFactory = Callable[..., AsyncContextManager[Any]]
HeadersFactory = Callable[[str], dict[str, str]]
McpPayloadFactory = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
UpstreamErrorDetail = Callable[[Any, str], str]
ValidateDatasetName = Callable[[str], None]


async def apps_payload_visible_and_ready(
    user: dict,
    *,
    include_unready: bool = False,
    cartridge: str | None = None,
    load_apps_payload: LoadAppsPayload,
    require_cartridge_visible: RequireCartridgeVisible,
    active_scoped_connection_cartridges: ResolveCartridges,
    installed_scoped_app_cartridges: ResolveCartridges,
    gold_ready_datasets_for_apps: GoldReadiness,
) -> dict[str, Any]:
    payload = await load_apps_payload(user)
    requested_cartridge = str(cartridge or "").strip()
    if requested_cartridge:
        require_cartridge_visible(user, requested_cartridge)
    payload_candidates = app_payload_cartridge_candidates(payload)
    candidates = {requested_cartridge} if requested_cartridge else payload_candidates
    active_cartridges = await active_scoped_connection_cartridges(user, candidates)
    scope_mode = "active_connections"
    if include_unready and not active_cartridges:
        active_cartridges = await installed_scoped_app_cartridges(user, candidates)
        if active_cartridges:
            scope_mode = "installed_cartridges"
    scoped_payload = filter_apps_payload_to_scoped_connections(
        payload, active_cartridges, scope_mode=scope_mode
    )
    ready_datasets, readiness_mode = await gold_ready_datasets_for_apps(
        user, scoped_payload
    )
    return filter_apps_payload_to_ready_datasets(
        scoped_payload,
        ready_datasets,
        mode=readiness_mode,
        include_unready=include_unready,
    )


async def load_refinement_apps_payload(
    *,
    user: dict[str, Any],
    http_client_factory: HttpClientFactory,
    headers_factory: HeadersFactory,
    mcp_payload: McpPayloadFactory,
    refinement_url: str,
    upstream_error_detail: UpstreamErrorDetail,
) -> Any:
    async with http_client_factory(
        headers=headers_factory("REFINEMENT"),
        timeout=10,
    ) as client:
        response = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload("list_apps", {}, user),
        )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            upstream_error_detail(response, "Apps service unavailable"),
        )
    return response.json()


async def refinement_app_html(
    *,
    name: str,
    user: dict[str, Any] | None,
    validate_dataset_name: ValidateDatasetName,
    http_client_factory: HttpClientFactory,
    headers_factory: HeadersFactory,
    mcp_payload: McpPayloadFactory,
    refinement_url: str,
    upstream_error_detail: UpstreamErrorDetail,
) -> tuple[str, dict[str, Any]]:
    validate_dataset_name(name)
    async with http_client_factory(
        headers=headers_factory("REFINEMENT"),
        timeout=10,
    ) as client:
        response = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload("get_app_html", {"name": name}, user or {}),
        )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            upstream_error_detail(response, "App content unavailable"),
        )
    payload = response.json()
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(
            404,
            str((result or {}).get("error") or f"App '{name}' not found"),
        )
    html_text = str(result.get("html") or "")
    if not html_text.strip():
        raise HTTPException(404, f"App '{name}' has no HTML content")
    return html_text, result


async def delete_refinement_app_payload(
    *,
    name: str,
    user: dict[str, Any],
    http_client_factory: HttpClientFactory,
    headers_factory: HeadersFactory,
    mcp_payload: McpPayloadFactory,
    refinement_url: str,
) -> dict[str, Any]:
    async with http_client_factory(
        headers=headers_factory("REFINEMENT"), timeout=10
    ) as client:
        response = await client.post(
            f"{refinement_url}/mcp/invoke",
            json=mcp_payload("delete_app", {"name": name}, user),
        )
    payload = response.json()
    result = payload.get("result", payload)
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or f"App '{name}' not found")
    return result

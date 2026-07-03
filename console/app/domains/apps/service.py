"""Application gallery orchestration helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.domains.apps.payloads import (
    app_payload_cartridge_candidates,
    filter_apps_payload_to_ready_datasets,
    filter_apps_payload_to_scoped_connections,
)


LoadAppsPayload = Callable[[dict], Awaitable[Any]]
RequireCartridgeVisible = Callable[[dict, str], None]
ResolveCartridges = Callable[[dict | None, set[str]], Awaitable[set[str]]]
GoldReadiness = Callable[[dict | None, Any], Awaitable[tuple[set[str] | None, str]]]


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

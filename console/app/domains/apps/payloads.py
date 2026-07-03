"""Pure Apps payload helpers shared by viewers and Control Room surfaces."""

from __future__ import annotations

import re
from typing import Any


DATASET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def apps_from_payload(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        apps = payload
    elif isinstance(payload, dict):
        raw_apps = payload.get("apps")
        raw_result = payload.get("result")
        apps = raw_apps if isinstance(raw_apps, list) else raw_result
    else:
        apps = []
    if not isinstance(apps, list):
        return []
    return [app for app in apps if isinstance(app, dict)]


def app_cartridge_id(app: dict) -> str:
    for key in ("cartridge", "cartridge_id", "connector_id"):
        value = str(app.get(key) or "").strip()
        if value:
            return value
    name = str(app.get("name") or app.get("id") or "").strip()
    for cartridge in (
        "sap_successfactors",
        "sap_s4hana",
        "sap_hcm",
        "salesforce",
        "replicon",
        "hubspot",
    ):
        if name == cartridge or name.startswith(f"{cartridge}_"):
            return cartridge
    return ""


def app_payload_cartridge_candidates(payload: Any) -> set[str]:
    return {
        cartridge
        for app in apps_from_payload(payload)
        if (cartridge := app_cartridge_id(app))
    }


def user_with_apps_scope(
    user: dict | None, tenant_id: str, workspace_id: str
) -> dict | None:
    if not user:
        return user
    if not tenant_id or not workspace_id:
        return user
    return {
        **user,
        "tenant_id": user.get("tenant_id") or tenant_id,
        "workspace_id": user.get("workspace_id") or workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


def filter_apps_payload_to_scoped_connections(
    payload: Any,
    active_cartridges: set[str],
    *,
    scope_mode: str = "active_connections",
) -> dict[str, Any]:
    if isinstance(payload, list):
        normalized: dict[str, Any] = {"apps": payload}
    elif isinstance(payload, dict):
        normalized = dict(payload)
    else:
        normalized = {"apps": []}

    apps = normalized.get("apps")
    result = normalized.get("result")
    apps_key = "apps"
    if not isinstance(apps, list) and isinstance(result, list):
        apps = result
        apps_key = "result"
    if not isinstance(apps, list):
        apps = []
    normalized["apps"] = apps

    visible_apps = [
        app
        for app in apps
        if isinstance(app, dict) and app_cartridge_id(app) in active_cartridges
    ]
    normalized[apps_key] = visible_apps
    normalized["apps"] = visible_apps
    normalized["active_scoped_cartridges"] = sorted(active_cartridges)
    normalized["apps_scope"] = {
        "mode": scope_mode if active_cartridges else "no_active_connections",
        "hidden_unconfigured_count": max(0, len(apps) - len(visible_apps)),
        "message": (
            "Apps filtradas por conexiones activas del workspace."
            if active_cartridges and scope_mode == "active_connections"
            else "Apps filtradas por cartuchos instalados; algunas pueden requerir datos materializados."
            if active_cartridges
            else "No hay apps configuradas para conexiones activas del workspace."
        ),
    }
    return normalized


def app_declared_datasets(app: dict) -> set[str]:
    datasets: set[str] = set()
    for key in ("datasets_used", "datasets", "dataset"):
        raw = app.get(key)
        if isinstance(raw, (list, tuple, set)):
            values = raw
        elif isinstance(raw, str):
            values = [raw]
        else:
            values = []
        for item in values:
            dataset = str(item or "").strip()
            if DATASET_NAME_RE.fullmatch(dataset):
                datasets.add(dataset)
    return datasets


def app_datasets_from_payload(payload: Any) -> set[str]:
    datasets: set[str] = set()
    for app in apps_from_payload(payload):
        datasets.update(app_declared_datasets(app))
    return datasets

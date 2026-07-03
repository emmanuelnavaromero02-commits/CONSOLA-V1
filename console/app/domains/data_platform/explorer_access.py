from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def resolve_explorer_bucket(
    bucket: str,
    default_buckets: list[dict[str, str]],
    *,
    is_security_admin: bool,
    lakehouse_bucket: str = "lakehouse",
) -> str:
    allowed: dict[str, str] = {}
    for item in default_buckets:
        name = item.get("name") or ""
        bid = item.get("id") or ""
        if bid and name:
            allowed[bid] = name
            allowed[name] = name
    resolved = allowed.get(bucket)
    if not resolved:
        raise HTTPException(403, "bucket not allowed")
    if not is_security_admin and bucket not in {"lakehouse", lakehouse_bucket}:
        raise HTTPException(403, "bucket requires admin role")
    return resolved


def explorer_quicklinks_for_cartridges(
    active_cartridges: set[str],
) -> list[dict[str, str]]:
    quicklinks: list[dict[str, str]] = []
    for cartridge in sorted(active_cartridges):
        label = cartridge.replace("_", " ")
        quicklinks.extend(
            [
                {
                    "label": f"Raw - {label}",
                    "bucket": "lakehouse",
                    "prefix": f"raw/{cartridge}/",
                },
                {
                    "label": f"Silver - {label}",
                    "bucket": "lakehouse",
                    "prefix": f"silver/{cartridge}/",
                },
                {
                    "label": f"Gold - {label}",
                    "bucket": "lakehouse",
                    "prefix": f"gold/{cartridge}/",
                },
            ]
        )
    return quicklinks


def explorer_path_allowed(
    path: str,
    context: dict[str, Any],
    *,
    is_security_admin: bool,
    object_access: bool = False,
) -> bool:
    path = (path or "").lstrip("/")
    if is_security_admin:
        return True
    if not path:
        return False

    allowed = {
        str(item).strip().strip("/")
        for item in (context.get("allowed_cartridges") or [])
        if str(item).strip().strip("/")
    }
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return False
    root, cartridge = parts[0], parts[1]
    if cartridge not in allowed and "*" not in allowed:
        return False
    if root == "cartridges":
        return True
    if root not in {"raw", "silver", "gold", "uploads"}:
        return False

    tenant = str(context.get("tenant_id") or "").strip()
    workspace = str(context.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        return True

    if "*" in allowed:
        # Workspace-scoped callers must browse only explicit cartridge
        # entitlements. A wildcard at this layer means auth enrichment failed,
        # so fail closed instead of exposing technical storage roots.
        return False

    tenant_marker = f"tenant_id={tenant}"
    workspace_marker = f"workspace_id={workspace}"
    tenant_parts = [part for part in parts if part.startswith("tenant_id=")]
    workspace_parts = [part for part in parts if part.startswith("workspace_id=")]
    if tenant_parts and any(part != tenant_marker for part in tenant_parts):
        return False
    if workspace_parts and any(part != workspace_marker for part in workspace_parts):
        return False
    if workspace_parts and not tenant_parts:
        return False

    if object_access:
        return bool(tenant_parts and workspace_parts)
    if tenant_parts or workspace_parts:
        return True
    if root in {"raw", "silver", "gold"}:
        return len(parts) <= 3
    if root == "uploads":
        return len(parts) <= 2
    return False

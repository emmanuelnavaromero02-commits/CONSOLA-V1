"""Tenant/workspace visibility helpers for technical data sources."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from app.dependencies import ROLE_ADMIN
from app.services.security_context import build_security_context


OPERATIONAL_CARTRIDGES = {
    "banxico",
    "hubspot",
    "inegi",
    "replicon",
    "salesforce",
    "sec_edgar",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "sap_b1",
}

TECHNICAL_SOURCE_PREFIXES = ("raw/", "silver/", "gold/", "uploads/", "cartridges/")


def is_security_admin_context(ctx: dict) -> bool:
    role = str(ctx.get("role") or "").lower()
    if not (bool(ctx.get("trusted")) and role in {ROLE_ADMIN, "owner", "super_admin"}):
        return False
    if ctx.get("tenant_id") or ctx.get("workspace_id"):
        return False
    allowed = {
        str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()
    }
    return "*" in allowed


def allowed_cartridges_for_user(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    raw = ctx.get("allowed_cartridges") or (user or {}).get("allowed_cartridges") or []
    allowed = {str(item).strip() for item in raw if str(item).strip()}
    if "*" in allowed or not allowed:
        return (
            None
            if str((user or {}).get("role") or "").lower()
            in {"owner", "super_admin", ROLE_ADMIN}
            else set()
        )
    return allowed


def scope_ids_from_user(user: dict | None) -> tuple[str, str]:
    ctx = build_security_context(user)
    return (
        str(ctx.get("tenant_id") or "").strip(),
        str(ctx.get("workspace_id") or "").strip(),
    )


def is_workspace_scoped_user(user: dict | None) -> bool:
    tenant_id, workspace_id = scope_ids_from_user(user)
    return bool(tenant_id and workspace_id)


def user_allowed_cartridges(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    if is_security_admin_context(ctx):
        return None
    role = str(ctx.get("role") or (user or {}).get("role") or "").strip().lower()
    if role in {"owner", "super_admin", ROLE_ADMIN}:
        return None
    allowed = {
        str(c).strip()
        for c in (ctx.get("allowed_cartridges") or [])
        if str(c).strip() and str(c).strip() != "*"
    }
    return allowed


def context_visible_cartridges(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    if is_security_admin_context(ctx):
        return None
    allowed = {
        str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()
    }
    if "*" in allowed:
        return None
    if allowed:
        return allowed
    if is_workspace_scoped_user(user):
        return set()
    role = str(ctx.get("role") or (user or {}).get("role") or "").strip().lower()
    if role in {"owner", "super_admin", ROLE_ADMIN}:
        return None
    return set()


def require_workspace_scope_for_technical_view(user: dict | None) -> None:
    if user_allowed_cartridges(user) is None:
        return
    if not is_workspace_scoped_user(user):
        raise HTTPException(403, "tenant/workspace scope required")


def cartridge_allowed_for_user(user: dict | None, cartridge_id: str) -> bool:
    cartridge_id = str(cartridge_id or "").strip()
    if not cartridge_id:
        return False
    allowed = user_allowed_cartridges(user)
    return allowed is None or cartridge_id in allowed


def require_technical_cartridge_access(user: dict | None, cartridge_id: str) -> None:
    if not cartridge_allowed_for_user(user, cartridge_id):
        raise HTTPException(403, "cartridge not allowed for active workspace")


def cartridge_from_technical_source(source: str) -> str:
    value = str(source or "").strip().strip("/")
    if not value:
        return ""
    for prefix in ("raw", "silver", "gold", "uploads", "cartridges"):
        marker = f"{prefix}/"
        if value.startswith(marker):
            parts = value.split("/")
            return parts[1] if len(parts) > 1 else ""
    for cartridge in sorted(OPERATIONAL_CARTRIDGES, key=len, reverse=True):
        if (
            value == cartridge
            or value.startswith(f"{cartridge}/")
            or value.startswith(f"{cartridge}:")
            or value.startswith(f"{cartridge}_")
        ):
            return cartridge
    return ""


def technical_source_allowed(user: dict | None, source: str) -> bool:
    allowed = user_allowed_cartridges(user)
    if allowed is None:
        return True
    require_workspace_scope_for_technical_view(user)
    cartridge = cartridge_from_technical_source(source)
    if not cartridge or cartridge not in allowed:
        return False
    tenant_id, workspace_id = scope_ids_from_user(user)
    parts = str(source or "").strip("/").split("/")
    tenant_markers = [part for part in parts if part.startswith("tenant_id=")]
    workspace_markers = [part for part in parts if part.startswith("workspace_id=")]
    if tenant_markers and any(
        part != f"tenant_id={tenant_id}" for part in tenant_markers
    ):
        return False
    if workspace_markers and any(
        part != f"workspace_id={workspace_id}" for part in workspace_markers
    ):
        return False
    if workspace_markers and not tenant_markers:
        return False
    return True


def require_technical_source_access(user: dict | None, source: str) -> None:
    if not technical_source_allowed(user, source):
        raise HTTPException(403, "source not allowed for active workspace")


def filter_technical_sources(user: dict | None, sources: list) -> list:
    return [
        source
        for source in sources
        if isinstance(source, str) and technical_source_allowed(user, source)
    ]


def technical_source_from_storage_reference(source: str) -> str | None:
    value = str(source or "").strip().strip("/")
    if not value:
        return None
    if value.startswith(TECHNICAL_SOURCE_PREFIXES):
        return value
    parsed = urlparse(value)
    if parsed.scheme:
        candidate = parsed.path.lstrip("/")
        if candidate.startswith(TECHNICAL_SOURCE_PREFIXES):
            return candidate
    return None


def dataset_source_visible_for_user(user: dict | None, source: Any) -> bool:
    if not isinstance(source, str):
        return False
    value = source.strip()
    if not value:
        return False
    if user_allowed_cartridges(user) is None:
        return True
    is_physical_reference = "://" in value or "tenant_id=" in value or "workspace_id=" in value
    candidate = technical_source_from_storage_reference(value)
    if candidate is not None:
        if is_physical_reference:
            tenant_id, workspace_id = scope_ids_from_user(user)
            if (
                not tenant_id
                or not workspace_id
                or f"tenant_id={tenant_id}" not in candidate
                or f"workspace_id={workspace_id}" not in candidate
            ):
                return False
        try:
            return technical_source_allowed(user, candidate)
        except HTTPException:
            return False
    if is_physical_reference:
        return False
    return True


def filter_dataset_sources_for_user(user: dict | None, sources: Any) -> list[str]:
    if not isinstance(sources, list):
        return []
    return [
        source.strip()
        for source in sources
        if dataset_source_visible_for_user(user, source)
    ]


def sanitize_dataset_metadata_for_user(user: dict | None, dataset: dict) -> dict:
    sanitized = {**dataset}
    sanitized["sources"] = filter_dataset_sources_for_user(
        user, sanitized.get("sources") or []
    )
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict):
        sanitized["metadata"] = {
            **metadata,
            "sources": filter_dataset_sources_for_user(user, metadata.get("sources") or []),
        }
    return sanitized


def sanitize_datasets_payload_for_user(user: dict | None, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return payload
    return {
        **payload,
        "datasets": [
            sanitize_dataset_metadata_for_user(user, dataset)
            for dataset in datasets
            if isinstance(dataset, dict)
        ],
    }

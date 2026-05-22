from __future__ import annotations

from typing import Any

from app.services import permissions


ADMIN_ROLES = {"admin", "owner", "super_admin"}


def build_security_context(user: dict | None) -> dict[str, Any]:
    """Build the server-owned context forwarded to internal MCP services.

    This payload is intentionally derived from the authenticated backend user,
    never from LLM/tool arguments. Downstream services should treat request
    body ``user_context`` fields as untrusted unless this context accompanies
    the internal request.
    """
    if not user:
        return {
            "trusted": False,
            "permissions": [],
            "role": "anonymous",
            "allowed_cartridges": [],
            "allowed_buckets": [],
            "allowed_prefixes": [],
        }

    role = permissions.user_role(user)
    workspace_role = permissions.workspace_role(user)
    effective = permissions.get_effective_permissions(user)
    explicit_cartridges = user.get("allowed_cartridges") if "allowed_cartridges" in user else user.get("cartridges")
    explicit_scope = explicit_cartridges is not None
    has_workspace_scope = bool(user.get("active_workspace_id") or user.get("workspace_id"))
    if explicit_scope:
        allowed_cartridges = list(explicit_cartridges or [])
    elif role in ADMIN_ROLES and not has_workspace_scope and ("datasets.read" in effective or "cartridges.read" in effective):
        allowed_cartridges = ["*"]
    else:
        allowed_cartridges = []

    return {
        "trusted": True,
        "source": "console",
        "user_id": user.get("id"),
        "email": user.get("email", ""),
        "role": role,
        "workspace_role": workspace_role,
        "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
        "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        "project_id": user.get("active_project_id") or user.get("project_id"),
        "permissions": sorted(effective),
        "allowed_cartridges": allowed_cartridges,
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": _allowed_prefixes(
            allowed_cartridges,
            role,
            tenant_id=user.get("active_tenant_id") or user.get("tenant_id"),
            workspace_id=user.get("active_workspace_id") or user.get("workspace_id"),
        ),
    }


def rls_user_context(user: dict | None) -> dict[str, Any]:
    """RLS context for refinement, derived from the same server-owned source."""
    ctx = build_security_context(user)
    return {
        "id": ctx.get("user_id"),
        "email": ctx.get("email", ""),
        "role": ctx.get("role"),
        "tenant_id": ctx.get("tenant_id"),
        "workspace_id": ctx.get("workspace_id"),
        "project_id": ctx.get("project_id"),
        "workspace_role": ctx.get("workspace_role"),
        "_server_trusted_context": bool(ctx.get("trusted")),
    }


def _allowed_prefixes(
    cartridges: list[str],
    role: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> list[str]:
    scoped = bool(tenant_id and workspace_id)
    if "*" in cartridges and not scoped:
        return ["raw/", "silver/", "gold/", "uploads/", "cartridges/", "inbound/", "inbound-processed/"]
    prefixes: list[str] = []
    for cart in cartridges:
        c = str(cart).strip().strip("/")
        if not c:
            continue
        if scoped:
            scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
            prefixes.extend([
                f"raw/{c}/{scope}",
                f"silver/{c}/{scope}",
                f"gold/{c}/{scope}",
                f"uploads/{c}/{scope}",
                f"cartridges/{c}/",
            ])
        else:
            prefixes.extend([
                f"raw/{c}/",
                f"silver/{c}/",
                f"gold/{c}/",
                f"uploads/{c}/",
                f"cartridges/{c}/",
            ])
    return prefixes

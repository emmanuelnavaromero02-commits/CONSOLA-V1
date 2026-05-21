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
    effective = permissions.get_effective_permissions(user, role=role)
    explicit_cartridges = user.get("allowed_cartridges") or user.get("cartridges")
    if explicit_cartridges:
        allowed_cartridges = explicit_cartridges
    elif role in ADMIN_ROLES and ("datasets.read" in effective or "cartridges.read" in effective):
        allowed_cartridges = ["*"]
    else:
        allowed_cartridges = []

    return {
        "trusted": True,
        "source": "console",
        "user_id": user.get("id"),
        "email": user.get("email", ""),
        "role": role,
        "workspace_role": user.get("workspace_role"),
        "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
        "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        "project_id": user.get("active_project_id") or user.get("project_id"),
        "permissions": sorted(effective),
        "allowed_cartridges": allowed_cartridges,
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": _allowed_prefixes(allowed_cartridges, role),
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


def _allowed_prefixes(cartridges: list[str], role: str) -> list[str]:
    if role in ADMIN_ROLES:
        return ["raw/", "silver/", "gold/", "uploads/", "cartridges/", "inbound/", "inbound-processed/"]
    if "*" in cartridges:
        return ["raw/", "silver/", "gold/", "uploads/", "cartridges/"]
    prefixes: list[str] = []
    for cart in cartridges:
        c = str(cart).strip().strip("/")
        if not c:
            continue
        prefixes.extend([
            f"raw/{c}/",
            f"silver/{c}/",
            f"gold/{c}/",
            f"uploads/{c}/",
            f"cartridges/{c}/",
        ])
    return prefixes

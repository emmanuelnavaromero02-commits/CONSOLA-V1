from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


PLATFORM_ADMIN_ROLES = {"owner", "super_admin", "admin"}
WORKSPACE_ADMIN_ROLES = {"workspace_admin", "tenant_admin"}


def switchable_workspaces(
    user: Mapping[str, Any],
    *,
    active_workspace_id: Any,
) -> list[dict[str, Any]]:
    workspaces: list[dict[str, Any]] = []
    for workspace in user.get("workspaces") or []:
        workspace_id = str(workspace.get("workspace_id") or "").strip()
        if not workspace_id:
            continue
        workspaces.append(
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace.get("workspace_name"),
                "tenant_id": workspace.get("tenant_id"),
                "tenant_name": workspace.get("tenant_name"),
                "workspace_role": workspace.get("workspace_role"),
                "active": workspace_id == str(active_workspace_id or ""),
            }
        )
    return workspaces


def access_ui_capabilities(
    effective_permissions: Iterable[str],
    *,
    role_canonical: str | None,
    workspace_role_resolved: str | None,
) -> dict[str, bool]:
    effective = set(effective_permissions)
    is_platform_admin = role_canonical in PLATFORM_ADMIN_ROLES
    can_manage_workspace = workspace_role_resolved in WORKSPACE_ADMIN_ROLES

    def _can(permission: str) -> bool:
        return permission in effective

    return {
        "can_view_iam": _can("iam.users.read") and is_platform_admin,
        "can_manage_companies": is_platform_admin,
        "can_manage_workspace_users": (
            _can("iam.users.read") and (is_platform_admin or can_manage_workspace)
        ),
        "can_admin_marketplace": _can("marketplace.admin"),
        "can_admin_workspace": can_manage_workspace,
        "can_view_audit": _can("security.audit.read"),
        "can_view_sessions": _can("security.sessions.read"),
        "can_view_dashboard": True,
        "can_view_workspace": _can("workspace.access"),
        "can_view_copilot": _can("copilot.use"),
        "can_view_knowledge": _can("mcp.registry.read") and is_platform_admin,
        "can_view_tokens": _can("copilot.use"),
        "can_manage_llm_key": _can("llm.keys.write"),
        "can_view_marketplace": _can("marketplace.read"),
        "can_view_apps": _can("apps.read"),
        "can_view_catalog": _can("datasets.read"),
        "can_view_lineage": _can("datasets.read"),
        "can_view_bronze": _can("datasets.write") and is_platform_admin,
        "can_view_explorer": _can("pipelines.read"),
        "can_view_studio": _can("studio.read") and is_platform_admin,
        "can_view_control_room": _can("workspace.access"),
        "can_view_monitor": _can("monitor.read"),
        "can_view_workflows": _can("operations.read") and is_platform_admin,
        "can_view_metrics": _can("operations.read"),
        "can_view_agents": _can("agents.read"),
        "can_manage_agents": _can("agents.write"),
        "can_execute_agents": _can("agents.execute"),
        "can_view_vault": _can("vault.connections.read"),
        "can_view_cartridges": _can("cartridges.read"),
        "can_view_settings": _can("settings.read") and is_platform_admin,
        "can_view_security": _can("security.audit.read") and is_platform_admin,
        "can_view_decisions": is_platform_admin,
    }


def me_access_payload(
    user: Mapping[str, Any],
    *,
    effective_permissions: Sequence[str],
    role_canonical: str | None,
    workspace_role_resolved: str | None,
    cartridges_allowed: Sequence[Mapping[str, Any]],
    cartridges_denied: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    active_workspace_id = user.get("workspace_id") or user.get("active_workspace_id")
    active_tenant_id = user.get("tenant_id") or user.get("active_tenant_id")
    is_platform_admin = role_canonical in PLATFORM_ADMIN_ROLES

    return {
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name") or user.get("email"),
        },
        "role": {
            "global": role_canonical,
            "is_platform_admin": is_platform_admin,
        },
        "workspace": {
            "tenant_id": active_tenant_id,
            "workspace_id": active_workspace_id,
            "workspace_role": workspace_role_resolved,
        },
        "workspaces": switchable_workspaces(
            user,
            active_workspace_id=active_workspace_id,
        ),
        "permissions": list(effective_permissions),
        "cartridges": {
            "allowed": list(cartridges_allowed),
            "denied": list(cartridges_denied),
        },
        "ui_capabilities": access_ui_capabilities(
            effective_permissions,
            role_canonical=role_canonical,
            workspace_role_resolved=workspace_role_resolved,
        ),
    }

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from fastapi import HTTPException, Request


PERMISSIONS = [
    {"key": "iam.users.read", "label": "Read users", "category": "IAM", "description": "List IAM users and account state."},
    {"key": "iam.users.write", "label": "Write users", "category": "IAM", "description": "Create, update, disable and reset users."},
    {"key": "iam.roles.read", "label": "Read roles", "category": "IAM", "description": "View roles and permissions."},
    {"key": "iam.roles.write", "label": "Write roles", "category": "IAM", "description": "Change role policy definitions."},
    {"key": "iam.policies.read", "label": "Read policies", "category": "IAM", "description": "View security policies and lifetimes."},
    {"key": "iam.policies.write", "label": "Write policies", "category": "IAM", "description": "Change IAM policies."},
    {"key": "security.audit.read", "label": "Read audit", "category": "Security", "description": "View audit events."},
    {"key": "security.sessions.read", "label": "Read sessions", "category": "Security", "description": "View active sessions."},
    {"key": "security.sessions.revoke", "label": "Revoke sessions", "category": "Security", "description": "Revoke active sessions."},
    {"key": "security.login_attempts.read", "label": "Read login attempts", "category": "Security", "description": "View login attempts."},
    {"key": "vault.connections.read", "label": "Read vault connections", "category": "Vault", "description": "List masked connection metadata."},
    {"key": "vault.connections.write", "label": "Write vault connections", "category": "Vault", "description": "Create/update/delete connection metadata."},
    {"key": "vault.secrets.read_masked", "label": "Read masked secrets", "category": "Vault", "description": "List masked secret metadata."},
    {"key": "vault.secrets.reveal", "label": "Reveal secrets", "category": "Vault", "description": "Reveal secret values."},
    {"key": "datasets.read", "label": "Read datasets", "category": "Data", "description": "Read dataset metadata and data."},
    {"key": "datasets.write", "label": "Write datasets", "category": "Data", "description": "Create or refresh datasets."},
    {"key": "datasets.delete", "label": "Delete datasets", "category": "Data", "description": "Delete datasets."},
    {"key": "pipelines.read", "label": "Read pipelines", "category": "Pipelines", "description": "View pipelines and runs."},
    {"key": "pipelines.run", "label": "Run pipelines", "category": "Pipelines", "description": "Trigger pipeline runs."},
    {"key": "pipelines.write", "label": "Write pipelines", "category": "Pipelines", "description": "Modify pipeline configuration."},
    {"key": "studio.read", "label": "Read studio", "category": "Studio", "description": "View Studio resources."},
    {"key": "studio.write", "label": "Write studio", "category": "Studio", "description": "Modify Studio resources."},
    {"key": "monitor.read", "label": "Read monitor", "category": "Monitor", "description": "View monitor pages and job state."},
    {"key": "workspace.access", "label": "Access workspace", "category": "Workspace", "description": "Access workspace apps."},
    {"key": "mcp.registry.read", "label": "Read MCP registry", "category": "MCP", "description": "View registered MCP services."},
    {"key": "mcp.invoke", "label": "Invoke MCP", "category": "MCP", "description": "Invoke MCP tools."},
    {"key": "apps.read", "label": "Read apps", "category": "Apps", "description": "View analytic apps."},
    {"key": "apps.write", "label": "Write apps", "category": "Apps", "description": "Modify analytic apps."},
    {"key": "settings.read", "label": "Read settings", "category": "Settings", "description": "View system settings (masked secrets)."},
    {"key": "settings.write", "label": "Write settings", "category": "Settings", "description": "Edit/reveal/rotate system settings."},
]

PERMISSION_KEYS = {item["key"] for item in PERMISSIONS}

ROLE_DEFINITIONS = {
    "owner": {
        "label": "Owner",
        "description": "Full super-admin control across IAM, security, data, vault and operations.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
    },
    "super_admin": {
        "label": "Super Admin",
        "description": "Alias-level full control for owner-style administration.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
    },
    "admin": {
        "label": "Administrator",
        "description": "General console administrator. Existing admin users remain fully compatible.",
        "assignable": True,
        "builtin": True,
        "legacy": True,
    },
    "security_admin": {
        "label": "Security Admin",
        "description": "IAM and security operator without production pipeline powers by default.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
    },
    "workspace_admin": {
        "label": "Workspace Admin",
        "description": "Workspace/data/pipeline operator without global IAM or security audit powers.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
        "assignment_note": "Assignable in users.role; workspace membership assignment is not wired yet.",
    },
    "analyst": {
        "label": "Analyst",
        "description": "Read/query datasets, monitor operations and use safe Studio read flows.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
        "assignment_note": "Assignable in users.role; workspace membership assignment is not wired yet.",
    },
    "auditor": {
        "label": "Auditor",
        "description": "Read-only security visibility for audit, sessions, permissions and policies.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
    },
    "viewer": {
        "label": "Viewer",
        "description": "Safe read-only console visibility. No IAM, Vault API or writes.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
        "assignment_note": "Assignable in users.role; workspace membership assignment is not wired yet.",
    },
    "workspace_user": {
        "label": "Workspace User",
        "description": "Workspace/app access only; no global console administration.",
        "assignable": True,
        "builtin": True,
        "legacy": False,
    },
    "user": {
        "label": "Legacy User",
        "description": "Existing legacy user role mapped to a conservative viewer/workspace_user permission set.",
        "assignable": True,
        "builtin": True,
        "legacy": True,
        "canonical_alias": "viewer",
    },
}

ROLE_PERMISSIONS = {
    "owner": set(PERMISSION_KEYS),
    "super_admin": set(PERMISSION_KEYS),
    "admin": PERMISSION_KEYS - {"iam.roles.write", "iam.policies.write"},
    "security_admin": {
        "iam.users.read", "iam.users.write", "iam.roles.read", "iam.policies.read",
        "security.audit.read", "security.sessions.read", "security.sessions.revoke",
        "security.login_attempts.read", "vault.connections.read", "vault.secrets.read_masked",
        "monitor.read",
    },
    "workspace_admin": {
        "datasets.read", "datasets.write", "datasets.delete",
        "pipelines.read", "pipelines.run", "pipelines.write",
        "studio.read", "studio.write", "monitor.read", "workspace.access",
        "vault.connections.read", "vault.connections.write",
        "vault.secrets.read_masked", "apps.read", "apps.write",
    },
    "analyst": {
        "datasets.read", "pipelines.read", "studio.read", "monitor.read",
        "workspace.access", "apps.read",
    },
    "auditor": {
        "iam.roles.read", "iam.policies.read", "security.audit.read",
        "security.sessions.read", "security.login_attempts.read", "monitor.read",
    },
    "viewer": {"monitor.read", "workspace.access", "apps.read", "studio.read", "pipelines.read", "datasets.read"},
    "workspace_user": {"workspace.access", "apps.read"},
    "user": {"monitor.read", "workspace.access", "apps.read", "studio.read"},
}

RESOURCE_ACTION_PERMISSIONS = {
    ("/iam", "read"): "iam.users.read",
    ("/security", "read"): "security.audit.read",
    ("/security/audit", "read"): "security.audit.read",
    ("/security/sessions", "read"): "security.sessions.read",
    ("/security/sessions", "revoke"): "security.sessions.revoke",
    ("/security/permissions", "read"): "iam.roles.read",
    ("/security/login-attempts", "read"): "security.login_attempts.read",
    ("/api/admin/users", "read"): "iam.users.read",
    ("/api/admin/users", "write"): "iam.users.write",
    ("/viewer/vault", "read"): "monitor.read",
    ("/api/vault/connections/replicon", "read"): "vault.connections.read",
    ("/api/vault/connections/replicon", "write"): "vault.connections.write",
    ("/api/vault/secrets/replicon", "read"): "vault.secrets.read_masked",
    ("/api/vault/secrets/replicon", "reveal"): "vault.secrets.reveal",
    ("/api/pipeline", "read"): "pipelines.read",
    ("/api/pipeline/run", "run"): "pipelines.run",
    ("/datasets", "read"): "datasets.read",
    ("/api/datasets", "write"): "datasets.write",
    ("/api/datasets", "delete"): "datasets.delete",
    ("/studio", "read"): "studio.read",
    ("/studio", "write"): "studio.write",
    ("/monitor", "read"): "monitor.read",
    ("/workspace", "access"): "workspace.access",
}


def canonical_role(role: str | None) -> str:
    value = (role or "user").strip() or "user"
    return value if value in ROLE_DEFINITIONS else "__unknown__"


def user_role(user: dict | None) -> str:
    if not user:
        return "anonymous"
    return canonical_role(user.get("workspace_role") or user.get("role"))


def get_effective_permissions(user: dict | None = None, role: str | None = None) -> set[str]:
    resolved = canonical_role(role or user_role(user))
    return set(ROLE_PERMISSIONS.get(resolved, set()))


def has_permission(user: dict | None, permission: str) -> bool:
    if permission not in PERMISSION_KEYS:
        return False
    return permission in get_effective_permissions(user)


def require_permission(permission: str) -> Callable:
    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"permission required: {permission}")
        return user

    return dependency


def require_any_permission(*permissions: str) -> Callable:
    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        effective = get_effective_permissions(user)
        if not any(permission in effective for permission in permissions):
            raise HTTPException(status_code=403, detail="required permission missing")
        return user

    return dependency


def roles_payload() -> list[dict]:
    result = []
    for name, info in ROLE_DEFINITIONS.items():
        item = deepcopy(info)
        item["name"] = name
        item["permissions"] = sorted(ROLE_PERMISSIONS.get(name, set()))
        result.append(item)
    return result


def matrix_payload() -> dict[str, dict[str, bool]]:
    return {
        role: {permission: permission in ROLE_PERMISSIONS.get(role, set()) for permission in sorted(PERMISSION_KEYS)}
        for role in ROLE_DEFINITIONS
    }


def permission_for_resource(resource: str, action: str) -> str | None:
    if (resource, action) in RESOURCE_ACTION_PERMISSIONS:
        return RESOURCE_ACTION_PERMISSIONS[(resource, action)]
    if action == "read" and resource.startswith("/security/audit"):
        return "security.audit.read"
    if action == "read" and resource.startswith("/security/sessions"):
        return "security.sessions.read"
    if action == "read" and resource.startswith("/api/admin/users"):
        return "iam.users.read"
    if action in {"write", "create", "update", "delete"} and resource.startswith("/api/admin/users"):
        return "iam.users.write"
    if resource.startswith("/api/vault/connections"):
        return "vault.connections.write" if action in {"write", "create", "update", "delete"} else "vault.connections.read"
    if resource.startswith("/api/vault/secrets") and action == "reveal":
        return "vault.secrets.reveal"
    if resource.startswith("/api/vault/secrets"):
        return "vault.secrets.read_masked" if action == "read" else "vault.connections.write"
    return None


def access_check(role: str, resource: str, action: str) -> dict:
    resolved_role = canonical_role(role)
    permission = permission_for_resource(resource, action)
    allowed = bool(permission and permission in ROLE_PERMISSIONS.get(resolved_role, set()))
    return {
        "role": resolved_role,
        "resource": resource,
        "action": action,
        "allowed": allowed,
        "permission": permission,
        "source": "backend_permission_registry",
        "reason": "No permission mapped for this resource/action." if not permission else (
            f"{resolved_role} includes {permission}." if allowed else f"{resolved_role} does not include {permission}."
        ),
    }

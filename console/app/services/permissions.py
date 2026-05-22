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
    {"key": "operations.read", "label": "Read operations", "category": "Operations", "description": "View system migrations and service health."},
    {"key": "operations.write", "label": "Write operations", "category": "Operations", "description": "Trigger operational actions."},
    # Sprint v1.41.0 — auditor P1 operativa: admins configure cartridge
    # connections + trigger extractions from the console. Modelled after
    # the pipelines.{run,write} split.
    {"key": "cartridges.read", "label": "Read cartridges", "category": "Cartridges", "description": "List cartridges, view entities and watermarks."},
    {"key": "cartridges.write", "label": "Configure cartridges", "category": "Cartridges", "description": "Edit connection metadata and run test_connection probes."},
    {"key": "cartridges.execute", "label": "Run cartridge extractions", "category": "Cartridges", "description": "Trigger entity extractions and knowledge-bit runs."},
    {"key": "marketplace.read", "label": "Read marketplace", "category": "Marketplace", "description": "View available cartridges and installation status."},
    {"key": "marketplace.request", "label": "Request marketplace products", "category": "Marketplace", "description": "Request activation of cartridge products for a workspace."},
    {"key": "marketplace.write", "label": "Legacy marketplace write", "category": "Marketplace", "description": "Backward-compatible marketplace write permission."},
    {"key": "marketplace.admin", "label": "Administer marketplace products", "category": "Marketplace", "description": "Approve, pause, revoke and reactivate cartridge entitlements."},
    # Sprint v1.42 — copilot RBAC. The brain in copilot_service.py maps
    # every tool's risk_level → required permission before invocation
    # (read tools → copilot.use, write tools → copilot.write, destructive
    # tools → copilot.execute plus an explicit user-approval card).
    {"key": "copilot.use",     "label": "Use the copilot", "category": "Copilot", "description": "Open the chat and invoke read-only tools."},
    {"key": "copilot.write",   "label": "Copilot writes", "category": "Copilot", "description": "Allow the copilot to invoke write-level tools on the user's behalf."},
    {"key": "copilot.execute", "label": "Copilot destructive actions", "category": "Copilot", "description": "Allow destructive tool calls — always behind a user-approval card."},
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
        # Workspace admins can manage identities inside their own workspace.
        # Routes still scope the visible/manageable users server-side; this
        # permission is not a platform-admin grant.
        "iam.users.read", "iam.users.write", "iam.roles.read",
        "datasets.read", "datasets.write", "datasets.delete",
        "pipelines.read", "pipelines.run", "pipelines.write",
        "studio.read", "studio.write", "monitor.read", "workspace.access",
        "vault.connections.read", "vault.connections.write",
        "vault.secrets.read_masked", "apps.read", "apps.write",
        "cartridges.read", "cartridges.write", "cartridges.execute",
        "marketplace.read", "marketplace.request",
        # v1.42: workspace admins drive the copilot end-to-end.
        "copilot.use", "copilot.write", "copilot.execute",
    },
    "analyst": {
        "datasets.read", "pipelines.read", "studio.read", "monitor.read",
        "workspace.access", "apps.read", "cartridges.read", "marketplace.read", "marketplace.request",
        # v1.42: analysts query data via the copilot — read-only.
        "copilot.use",
    },
    "auditor": {
        "iam.roles.read", "iam.policies.read", "security.audit.read",
        "security.sessions.read", "security.login_attempts.read", "monitor.read",
        # v1.42: auditors read via the copilot to investigate incidents.
        "copilot.use",
    },
    "viewer": {"monitor.read", "workspace.access", "apps.read", "studio.read", "pipelines.read", "datasets.read", "cartridges.read", "marketplace.read", "copilot.use"},
    "workspace_user": {"workspace.access", "apps.read", "marketplace.read", "marketplace.request"},
    "user": {"monitor.read", "workspace.access", "apps.read", "studio.read", "marketplace.read"},
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
    ("/marketplace", "read"): "marketplace.read",
    ("/marketplace", "request"): "marketplace.request",
    ("/admin/installations", "read"): "marketplace.admin",
    ("/admin/installations", "write"): "marketplace.admin",
}


def canonical_role(role: str | None) -> str:
    value = (role or "user").strip() or "user"
    return value if value in ROLE_DEFINITIONS else "__unknown__"


def user_role(user: dict | None) -> str:
    if not user:
        return "anonymous"
    # This is the global account role only. Workspace roles are intentionally
    # kept separate so a workspace admin cannot be treated as a platform admin
    # by downstream MCP/refinement services.
    return canonical_role(user.get("role"))


def workspace_role(user: dict | None) -> str | None:
    """Return the scoped workspace role, never a platform-admin role.

    The legacy database role name ``admin`` exists in ``user_workspace_roles``
    for old installs. Inside a workspace that means "admin of this workspace",
    not "admin of the whole platform". Normalize it before permission union so
    workspace membership cannot accidentally grant global IAM/Vault powers.
    """
    if not user or not user.get("workspace_role"):
        return None
    resolved = canonical_role(user.get("workspace_role"))
    if resolved in {"admin", "owner", "super_admin", "security_admin"}:
        return "workspace_admin"
    if resolved in ROLE_PERMISSIONS:
        return resolved
    return None


def get_effective_permissions(user: dict | None = None, role: str | None = None) -> set[str]:
    if role:
        resolved = canonical_role(role)
        return set(ROLE_PERMISSIONS.get(resolved, set()))
    if not user:
        return set()
    roles = {user_role(user)}
    scoped = workspace_role(user)
    if scoped:
        roles.add(scoped)
    if not roles:
        roles = {user_role(user)}
    effective: set[str] = set()
    for resolved in roles:
        effective.update(ROLE_PERMISSIONS.get(resolved, set()))
    return effective


def has_permission(user: dict | None, permission: str) -> bool:
    if permission not in PERMISSION_KEYS:
        return False
    if permission == "marketplace.admin":
        return canonical_role((user or {}).get("role")) in {"owner", "super_admin", "admin"}
    return permission in get_effective_permissions(user)


def require_permission(permission: str) -> Callable:
    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"permission required: {permission}")
        return user

    dependency.__name__ = f"require_permission_{permission.replace('.', '_')}"
    dependency.required_permission = permission  # type: ignore[attr-defined]
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

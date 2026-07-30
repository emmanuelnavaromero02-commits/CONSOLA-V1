from __future__ import annotations

from app.services.permission_catalog import PERMISSION_KEYS


_AUTHORITY_PERMISSIONS = {"control_room.approve"}

# fmt: off
ROLE_DEFINITIONS = {
    "owner": {"label": "Owner", "description": "Platform administration without maker-separated Control Room approval.", "assignable": True, "builtin": True, "legacy": False},
    "super_admin": {"label": "Super Admin", "description": "Platform administration without maker-separated Control Room approval.", "assignable": True, "builtin": True, "legacy": False},
    "admin": {"label": "Administrator", "description": "General administration without maker-separated Control Room approval.", "assignable": True, "builtin": True, "legacy": True},
    "security_admin": {"label": "Security Admin", "description": "IAM and security operator without production pipeline powers by default.", "assignable": True, "builtin": True, "legacy": False},
    "workspace_admin": {"label": "Workspace Admin", "description": "Workspace/data/pipeline operator without global IAM or security audit powers.", "assignable": True, "builtin": True, "legacy": False, "assignment_note": "Platform-assignable; tenant-created users receive workspace membership separately."},
    "tenant_admin": {"label": "Tenant Admin", "description": "Workspace-scoped account administrator without Studio, Bronze or global security access.", "assignable": True, "builtin": True, "legacy": False, "assignment_note": "Assignable only as a workspace role by non-platform admins."},
    "control_room_approver": {"label": "Control Room Approver", "description": "Checker for maker-separated internal Control Room actions.", "assignable": True, "builtin": True, "legacy": False, "assignment_note": "Grant explicitly per workspace; never implied by an administrative role."},
    "analyst": {"label": "Analyst", "description": "Read/query datasets, monitor operations and use safe Studio read flows.", "assignable": True, "builtin": True, "legacy": False, "assignment_note": "Assignable as a workspace role by tenant admins."},
    "auditor": {"label": "Auditor", "description": "Read-only security visibility for audit, sessions, permissions and policies.", "assignable": True, "builtin": True, "legacy": False},
    "viewer": {"label": "Viewer", "description": "Safe read-only console visibility. No IAM, Vault API or writes.", "assignable": True, "builtin": True, "legacy": False, "assignment_note": "Assignable as a workspace role by tenant admins."},
    "workspace_user": {"label": "Workspace User", "description": "Workspace/app access only; no global console administration.", "assignable": True, "builtin": True, "legacy": False},
    "user": {"label": "Legacy User", "description": "Existing legacy user role mapped to a conservative viewer/workspace_user permission set.", "assignable": True, "builtin": True, "legacy": True, "canonical_alias": "viewer"},
}

ROLE_PERMISSIONS = {
    "owner": PERMISSION_KEYS - _AUTHORITY_PERMISSIONS,
    "super_admin": PERMISSION_KEYS - _AUTHORITY_PERMISSIONS,
    "admin": PERMISSION_KEYS - {"iam.roles.write", "iam.policies.write", *_AUTHORITY_PERMISSIONS},
    "security_admin": {"iam.users.read", "iam.users.write", "iam.roles.read", "iam.policies.read", "security.audit.read", "security.sessions.read", "security.sessions.revoke", "security.login_attempts.read", "vault.connections.read", "vault.secrets.read_masked", "monitor.read"},
    "workspace_admin": {"iam.users.read", "iam.users.write", "iam.roles.read", "datasets.read", "datasets.write", "datasets.delete", "pipelines.read", "pipelines.run", "pipelines.write", "studio.read", "studio.write", "monitor.read", "workspace.access", "control_room.write", "control_room.execute", "vault.connections.read", "vault.connections.write", "vault.secrets.read_masked", "apps.read", "apps.write", "agents.read", "agents.write", "agents.execute", "cartridges.read", "cartridges.write", "cartridges.execute", "marketplace.read", "marketplace.request", "copilot.use", "copilot.write", "copilot.execute", "llm.keys.read", "llm.keys.write"},
    "tenant_admin": {"iam.users.read", "iam.users.write", "iam.roles.read", "security.audit.read", "vault.connections.read", "vault.connections.write", "vault.secrets.read_masked", "datasets.read", "pipelines.read", "pipelines.run", "monitor.read", "operations.read", "workspace.access", "control_room.write", "control_room.execute", "apps.read", "cartridges.read", "cartridges.write", "cartridges.execute", "marketplace.read", "marketplace.request", "agents.read", "agents.write", "agents.execute", "copilot.use", "llm.keys.read", "llm.keys.write"},
    "control_room_approver": {"workspace.access", "datasets.read", "control_room.approve", "control_room.execute"},
    "analyst": {"datasets.read", "pipelines.read", "monitor.read", "workspace.access", "apps.read", "cartridges.read", "marketplace.read", "marketplace.request", "agents.read", "agents.execute", "copilot.use"},
    "auditor": {"iam.roles.read", "iam.policies.read", "security.audit.read", "security.sessions.read", "security.login_attempts.read", "monitor.read", "copilot.use"},
    "viewer": {"monitor.read", "workspace.access", "apps.read", "pipelines.read", "datasets.read", "cartridges.read", "marketplace.read", "copilot.use"},
    "workspace_user": {"workspace.access", "apps.read", "marketplace.read", "marketplace.request"},
    "user": {"monitor.read", "workspace.access", "apps.read", "marketplace.read"},
}
# fmt: on

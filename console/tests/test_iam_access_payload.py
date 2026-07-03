from __future__ import annotations

import asyncio

from app.domains.iam.access_payload import (
    access_ui_capabilities,
    fetch_cartridge_access,
    me_access_payload,
    switchable_workspaces,
)


def test_switchable_workspaces_marks_active_workspace_and_skips_blank_ids():
    user = {
        "workspaces": [
            {
                "workspace_id": "ws-a",
                "workspace_name": "A",
                "tenant_id": "tenant-a",
                "tenant_name": "Tenant A",
                "workspace_role": "tenant_admin",
            },
            {"workspace_id": "  "},
            {"workspace_id": "ws-b", "workspace_name": "B"},
        ]
    }

    assert switchable_workspaces(user, active_workspace_id="ws-a") == [
        {
            "workspace_id": "ws-a",
            "workspace_name": "A",
            "tenant_id": "tenant-a",
            "tenant_name": "Tenant A",
            "workspace_role": "tenant_admin",
            "active": True,
        },
        {
            "workspace_id": "ws-b",
            "workspace_name": "B",
            "tenant_id": None,
            "tenant_name": None,
            "workspace_role": None,
            "active": False,
        },
    ]


def test_access_ui_capabilities_keep_platform_admin_gates():
    caps = access_ui_capabilities(
        {
            "iam.users.read",
            "settings.read",
            "security.audit.read",
            "studio.read",
            "workspace.access",
        },
        role_canonical="security_admin",
        workspace_role_resolved=None,
    )

    assert caps["can_view_iam"] is False
    assert caps["can_view_settings"] is False
    assert caps["can_view_security"] is False
    assert caps["can_view_studio"] is False
    assert caps["can_view_workspace"] is True


def test_access_ui_capabilities_allow_workspace_admin_user_management():
    caps = access_ui_capabilities(
        {"iam.users.read", "vault.connections.read"},
        role_canonical="user",
        workspace_role_resolved="tenant_admin",
    )

    assert caps["can_manage_workspace_users"] is True
    assert caps["can_admin_workspace"] is True
    assert caps["can_view_iam"] is False
    assert caps["can_view_vault"] is True


def test_me_access_payload_shape_and_capabilities():
    payload = me_access_payload(
        {
            "id": 7,
            "email": "ops@example.com",
            "active_tenant_id": "tenant-a",
            "active_workspace_id": "ws-a",
            "workspaces": [{"workspace_id": "ws-a", "workspace_role": "workspace_admin"}],
        },
        effective_permissions=["agents.read", "marketplace.admin", "workspace.access"],
        role_canonical="admin",
        workspace_role_resolved="workspace_admin",
        cartridges_allowed=[{"cartridge_id": "sap_successfactors"}],
        cartridges_denied=[{"cartridge_id": "replicon", "reason": "user_deny"}],
    )

    assert payload["user"] == {
        "id": 7,
        "email": "ops@example.com",
        "name": "ops@example.com",
    }
    assert payload["role"] == {"global": "admin", "is_platform_admin": True}
    assert payload["workspace"]["workspace_id"] == "ws-a"
    assert payload["workspaces"][0]["active"] is True
    assert payload["permissions"] == [
        "agents.read",
        "marketplace.admin",
        "workspace.access",
    ]
    assert payload["cartridges"]["allowed"] == [{"cartridge_id": "sap_successfactors"}]
    assert payload["cartridges"]["denied"] == [
        {"cartridge_id": "replicon", "reason": "user_deny"}
    ]
    assert payload["ui_capabilities"]["can_admin_marketplace"] is True
    assert payload["ui_capabilities"]["can_manage_companies"] is True
    assert payload["ui_capabilities"]["can_view_agents"] is True


def test_fetch_cartridge_access_uses_scoped_marketplace_queries():
    class _AsyncContext:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def __init__(self):
            self.calls = []

        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            if "FROM cartridge_installations ci" in sql:
                return [
                    {
                        "cartridge_id": "sap_successfactors",
                        "product_name": "SAP SuccessFactors",
                        "status": "ready",
                    }
                ]
            return [
                {
                    "cartridge_id": "replicon",
                    "product_name": "Replicon",
                    "installation_status": "ready",
                }
            ]

    class _Pool:
        def __init__(self):
            self.conn = _Conn()

        def acquire(self):
            return _AsyncContext(self.conn)

    pool = _Pool()

    async def _pool_factory():
        return pool

    allowed, denied = asyncio.run(
        fetch_cartridge_access(
            {
                "id": 7,
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
            },
            pool_factory=_pool_factory,
        )
    )

    assert allowed == [
        {
            "cartridge_id": "sap_successfactors",
            "product_name": "SAP SuccessFactors",
            "status": "ready",
        }
    ]
    assert denied == [
        {
            "cartridge_id": "replicon",
            "product_name": "Replicon",
            "reason": "user_deny",
            "installation_status": "ready",
        }
    ]
    assert pool.conn.calls[0][1] == ("tenant-a", "workspace-a", 7)
    assert pool.conn.calls[1][1] == ("tenant-a", "workspace-a", 7)

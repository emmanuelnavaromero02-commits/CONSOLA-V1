from __future__ import annotations

import re
from pathlib import Path

from tests.console_route_source import console_route_source


REPO = Path(__file__).resolve().parents[1]
MAIN_PY = REPO / "console/app/main.py"
SEC_PY = REPO / "console/app/routers/security.py"
TS_TYPES = REPO / "console-next/src/lib/operations/types.ts"
TS_CLIENT = REPO / "console-next/src/lib/operations/client.ts"
ADMIN_TENANTS_PY = REPO / "console/app/routers/admin_tenants.py"
ADMIN_PASSWORD_RESET_PY = REPO / "console/app/domains/admin/password_reset.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_admin_users_routes_use_real_verbs():
    src = console_route_source()
    expectations = [
        ("get", '"/api/admin/users"', "iam.users.read"),
        ("post", '"/api/admin/users"', "iam.users.write"),
        ("patch", '"/api/admin/users/{user_id}"', "iam.users.write"),
        ("delete", '"/api/admin/users/{user_id}"', "iam.users.write"),
        ("post", '"/api/admin/users/{user_id}/send-reset"', "iam.users.write"),
    ]
    for verb, route_literal, permission in expectations:
        pattern = re.compile(
            rf"@app\.{verb}\(\s*{re.escape(route_literal)}",
        )
        assert pattern.search(src), (
            f"Backend route @app.{verb}({route_literal}, ...) not found in "
            f"console/app/main.py. The Next.js Operations Users page "
            f"calls this verb+path."
        )
        block = re.search(
            rf"@app\.{verb}\(\s*{re.escape(route_literal)}[\s\S]*?\):\s*\n",
            src,
        )
        assert (
            block
        ), f"Decorator + signature block for {verb} {route_literal} not found"
        assert permission in block.group(0), (
            f"@app.{verb}({route_literal}) must enforce "
            f"require_permission({permission!r})"
        )


def test_security_audit_route_exists():
    src = _read(SEC_PY)
    assert 'prefix="/security"' in src, "security router must use prefix=/security"
    assert '@router.get("/audit")' in src
    assert (
        'require_permission("security.audit.read")' in src
    ), "GET /security/audit must require security.audit.read"


def test_vault_connections_route_exists():
    src = console_route_source()
    assert '"/api/vault/connections/{cartridge}"' in src
    assert 'require_permission("vault.connections.read")' in src


def test_company_onboarding_routes_exist():
    src = _read(ADMIN_TENANTS_PY)
    for route in (
        '@router.get("")',
        '@router.post("",',
        '@router.get("/{tenant_id}/workspaces")',
        '@router.post("/{tenant_id}/workspaces"',
        '@router.post("/{tenant_id}/bootstrap-admin"',
        '"/{tenant_id}/admins/{user_id}/temporary-password"',
    ):
        assert route in src
    assert 'require_global_any_role("owner", "super_admin", ROLE_ADMIN)' in src
    assert "temporary_password = _temporary_password()" in src
    assert "tenant_admin_temporary_password_issued" in src


def test_ts_audit_comment_says_patch_for_user_update():
    src = _read(TS_TYPES)
    assert "PATCH  /api/admin/users/{user_id}" in src, (
        "lib/operations/types.ts audit comment must say PATCH "
        "(not PUT) for the user update route"
    )


def test_ts_client_uses_patch_for_update_user():
    src = _read(TS_CLIENT)
    assert "api.patch<AppUser>" in src, (
        "lib/operations/client.ts:updateUser must use api.patch, "
        "matching the real backend verb"
    )


def test_admin_reset_returns_one_time_temporary_password():
    src = console_route_source()
    marker = "async def api_admin_users_send_reset"
    assert marker in src, "api_admin_users_send_reset block not found"
    start = src.index(marker)
    body = src[start : src.index("async def api_vault_list_connections", start)]
    reset_domain = _read(ADMIN_PASSWORD_RESET_PY)
    assert "_admin_send_reset_payload_impl" in body
    assert '"temporary_password": temporary_password' in reset_domain
    assert '"password_delivery": "one_time_response"' in reset_domain
    assert "must_change_password = TRUE" in reset_domain
    assert "omega_auth_revoke_user_tokens" in reset_domain
    assert "temporary_password_issued" in reset_domain


def test_operations_ui_surfaces_reset_temporary_password_once():
    users_table = _read(REPO / "console-next/src/components/operations/UsersTable.tsx")
    types_src = _read(TS_TYPES)
    client_src = _read(TS_CLIENT)

    assert "SendPasswordResetResponse" in types_src
    assert "temporary_password?: string | null" in types_src
    assert "Promise<SendPasswordResetResponse>" in client_src
    assert "Contraseña temporal generada" in users_table
    assert "navigator.clipboard.writeText(resetSecret.password)" in users_table


def test_ts_client_lists_audit_at_security_path():
    src = _read(TS_CLIENT)
    assert '"/security/audit"' in src
    assert "/api/audit" not in src


def test_ts_types_has_audit_comment_block():
    src = _read(TS_TYPES)
    assert "BACKEND AUDIT" in src, (
        "lib/operations/types.ts must keep the 'BACKEND AUDIT' "
        "header comment block — it's the index of where every "
        "endpoint claim came from."
    )

"""Sprint v1.44.4 Group 1 — pin the Operations backend contract.

The Next.js /operations/{users,audit,vault} pages render against
real backend endpoints. Three Round 1 review findings — most
critically the PUT-vs-PATCH mismatch on user updates — would have
broken the live UI silently if the shapes had drifted further.
This file pins:

  - User CRUD verbs + permission gates at the real paths.
  - Audit log path (/security/audit, NOT /api/audit).
  - Vault list path.
  - The TypeScript audit comment block agrees with the Python
    source it claims to mirror.

If a backend refactor renames a route or flips PATCH → PUT (or
vice versa), CI fires loud before the operator clicks Save and
gets a 405.
"""

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


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Backend route invariants ──────────────────────────────────


def test_admin_users_routes_use_real_verbs():
    """Pin the exact HTTP verbs on each admin-users route. The
    Round 1 Backend audit caught a PUT/PATCH mismatch — keep
    that fixed and any future verb change explicit."""
    src = console_route_source()
    # Each tuple is (verb, route literal, permission needed)
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
        # Permission check — every users route MUST gate behind
        # ``require_permission(<permission>)``. The permission
        # can appear in the decorator (``dependencies=[...]``)
        # OR in the function signature (``Depends(...)``), so we
        # capture from the route literal through the next blank
        # line / decorator boundary.
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
    """The Next.js Audit page reads /security/audit (NOT
    /api/audit). Pin the prefix and the permission gate."""
    src = _read(SEC_PY)
    assert 'prefix="/security"' in src, "security router must use prefix=/security"
    assert '@router.get("/audit")' in src
    assert (
        'require_permission("security.audit.read")' in src
    ), "GET /security/audit must require security.audit.read"


def test_vault_connections_route_exists():
    """The Next.js Vault page reads /api/vault/connections/{cartridge}."""
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


# ── Frontend types agree with backend ────────────────────────


def test_ts_audit_comment_says_patch_for_user_update():
    """The Round 1 Backend audit fixed a PUT → PATCH mismatch.
    Pin the docstring on types.ts so a future refactor that
    moves the verb has to come back here too."""
    src = _read(TS_TYPES)
    assert "PATCH  /api/admin/users/{user_id}" in src, (
        "lib/operations/types.ts audit comment must say PATCH "
        "(not PUT) for the user update route"
    )


def test_ts_client_uses_patch_for_update_user():
    """Belt-and-suspenders: the runtime code must use api.patch
    too, not just the docstring."""
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
    assert '"temporary_password": temporary_password' in body
    assert '"password_delivery": "one_time_response"' in body
    assert "must_change_password = TRUE" in body
    assert "DELETE FROM refresh_tokens" in body
    assert "DELETE FROM user_sessions" in body
    assert "temporary_password_issued" in body


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
    """The Audit page MUST hit /security/audit directly on FastAPI,
    NOT /api/audit/log."""
    src = _read(TS_CLIENT)
    assert '"/security/audit"' in src
    assert "/api/audit" not in src


def test_ts_types_has_audit_comment_block():
    """Force every new Operations endpoint to land in the
    audit-comment header so the Task-A class of bug
    (frontend-shape drift) keeps catching attention."""
    src = _read(TS_TYPES)
    assert "BACKEND AUDIT" in src, (
        "lib/operations/types.ts must keep the 'BACKEND AUDIT' "
        "header comment block — it's the index of where every "
        "endpoint claim came from."
    )

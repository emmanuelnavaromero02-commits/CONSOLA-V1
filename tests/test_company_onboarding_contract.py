from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ROUTER = REPO / "console/app/routers/admin_tenants.py"
MAIN = REPO / "console/app/main.py"
AUTH = REPO / "console/app/services/auth.py"
PAGES = REPO / "console/app/routers/pages.py"
MIGRATION = REPO / "infra/init/99m_company_onboarding_rls.sql"
COMPANIES_UI = REPO / "console-next/src/components/operations/CompaniesConsole.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_admin_tenants_router_is_registered_and_platform_only():
    router = _read(ROUTER)
    main = _read(MAIN)

    assert "from app.routers import admin_tenants as admin_tenants_router" in main
    assert "app.include_router(admin_tenants_router.router)" in main
    assert 'APIRouter(prefix="/api/admin/tenants"' in router
    assert 'require_global_any_role("owner", "super_admin", ROLE_ADMIN)' in router
    for route in (
        '@router.get("")',
        '@router.post("",',
        '@router.get("/{tenant_id}/workspaces")',
        '@router.post("/{tenant_id}/workspaces"',
        '@router.post("/{tenant_id}/bootstrap-admin"',
        '"/{tenant_id}/admins/{user_id}/temporary-password"',
    ):
        assert route in router
    assert "dependencies=[Depends(require_csrf)]" in router


def test_bootstrap_admin_generates_one_time_password_server_side():
    router = _read(ROUTER)

    assert "temporary_password = _temporary_password()" in router
    assert 'body.get("temporary_password")' not in router
    assert '"temporary_password": temporary_password' in router
    assert '"password_delivery": "one_time_response"' in router
    assert "auth.hash_password(temporary_password)" in router
    assert "DANGEROUS_GLOBAL_ROLES" in router
    assert "tenant_admin_temporary_password_issued" in router
    assert '"workspace_role": "tenant_admin"' in router
    assert 'body.get("temporary_password")' not in router
    for role in ("owner", "admin", "super_admin", "security_admin", "auditor"):
        assert role in router


def test_auth_create_user_writes_tenant_and_forces_password_change():
    auth = _read(AUTH)
    create_section = auth.split("async def create_user", 1)[1].split(
        "async def create_invited_user", 1
    )[0]

    assert "tenant_id: str | None = None" in create_section
    assert (
        "await _assignment_workspace(conn, workspace_id, tenant_id)" in create_section
    )
    assert "must_change_password, tenant_id" in create_section
    assert "TRUE, TRUE" in create_section
    assert "workspace does not belong to tenant" in auth


def test_companies_ui_and_route_are_real_not_legacy_redirects():
    pages = _read(PAGES)
    ui = _read(COMPANIES_UI)

    assert '"/operations/companies"' in pages
    assert "operations/companies/index.html" in pages
    assert (
        'PLATFORM_ADMIN = require_global_any_role("owner", "super_admin", ROLE_ADMIN)'
        in pages
    )
    assert (
        '"/operations/companies",\n    dependencies=[Depends(PLATFORM_ADMIN)]' in pages
    )
    assert "CompaniesConsole" in ui
    assert "useCreateTenant" in ui
    assert "useCreateTenantWorkspace" in ui
    assert "useBootstrapTenantAdmin" in ui
    assert "useIssueTenantAdminTemporaryPassword" in ui
    assert "Acciones de empresa" in ui
    assert "Reset temporal" in ui
    assert "Revocar" in ui
    assert "Este formulario no crea empresas" not in ui
    assert "Contraseña temporal" in ui


def test_company_onboarding_migration_adds_slug_and_documents_rls_allowlist():
    sql = _read(MIGRATION)

    assert "ADD COLUMN IF NOT EXISTS slug TEXT" in sql
    assert "uq_tenants_slug" in sql
    assert "omega_rls_platform_owner_allowlist" in sql
    for table in ("tenants", "workspaces", "users", "user_workspace_roles"):
        assert f"('{table}'" in sql
    for table in (
        "control_room_items",
        "prediction_outcomes",
        "pipeline_runs",
        "copilot_goals",
    ):
        assert f"'{table}'" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "USING (true)" not in sql

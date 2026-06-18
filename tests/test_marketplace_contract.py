import re
from pathlib import Path

from tests.console_route_source import console_route_source


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_marketplace_has_no_standalone_html_page():
    assert not (ROOT / "console/app/static/marketplace.html").exists()


def test_marketplace_routes_use_console_next_export():
    main = console_route_source()
    assert '@app.get("/marketplace"' in main
    assert '@app.get("/customer/cartridges"' in main
    assert '"/admin/installations"' in main
    assert '_console_next_response(request, "marketplace/index.html")' in main
    assert '_console_next_response(request, "customer/cartridges/index.html")' in main
    assert '_console_next_response(request, "admin/installations/index.html")' in main
    assert '_console_next_response(request, "admin/licenses/index.html")' in main


def test_marketplace_next_surface_keeps_customer_and_admin_flows():
    lib = read("console-next/src/lib/marketplace.ts")
    component = read("console-next/src/components/marketplace/MarketplaceConsole.tsx")
    navigation = read("console-next/src/components/AppChrome.tsx")
    sidebar = ROOT / "console-next/src/components/AppSidebar.tsx"
    if sidebar.exists():
        navigation += "\n" + sidebar.read_text(encoding="utf-8")
    for endpoint in (
        "/api/marketplace/products",
        "/api/customer/cartridges",
        "/api/marketplace/products/${encodeURIComponent(cartridgeId)}/request",
        "/api/marketplace/installations/${encodeURIComponent(installationId)}/retry",
        "/api/admin/installations",
        "/api/admin/installations/${encodeURIComponent(installationId)}/access",
        "/api/admin/installations/${encodeURIComponent(installationId)}/access/${encodeURIComponent(userId)}",
    ):
        assert endpoint in lib
    for action in ("approve", "pause", "revoke", "reactivate"):
        assert action in component
    assert 'href: "/marketplace"' in navigation
    assert '"can_view_marketplace"' in navigation
    assert "can_admin_marketplace" in component


def test_marketplace_next_static_pages_exist_after_export():
    static = ROOT / "console/app/static/console-next"
    for page in (
        "marketplace/index.html",
        "customer/cartridges/index.html",
        "admin/installations/index.html",
        "admin/licenses/index.html",
    ):
        assert (static / page).exists()


def test_marketplace_css_is_scoped_to_console_view():
    css = read("console/app/static/css/marketplace.css")
    assert ".marketplace-view" in css
    assert ".market-shell" not in css
    assert ".market-sidebar" not in css


def test_marketplace_migration_uses_existing_platform_models():
    sql = read("infra/init/73_marketplace_installations.sql")
    for table in ("cartridges", "tenants", "workspaces"):
        assert f"REFERENCES {table}" in sql
    assert "INSERT INTO marketplace_products" in sql
    assert "FROM cartridges" in sql
    assert "cartridge_installations_tenant_workspace_cartridge_uniq" in sql
    for status in (
        "requested",
        "paused",
        "revoked",
        "expired",
        "suspended",
        "pending_connection",
    ):
        assert status in sql


def test_marketplace_user_access_migration_scopes_per_user_without_parallel_model():
    sql = read("infra/init/75_user_cartridge_access.sql")
    assert "CREATE TABLE IF NOT EXISTS user_cartridge_overrides" in sql
    for table in ("tenants", "workspaces", "cartridges", "users"):
        assert f"REFERENCES {table}" in sql
    assert "PRIMARY KEY (tenant_id, workspace_id, cartridge_id, user_id)" in sql
    assert "CHECK (mode IN ('deny'))" in sql
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_cartridge_overrides TO omega_console"
        in sql
    )
    assert "GRANT SELECT ON user_cartridge_overrides TO omega_workspace" in sql


def test_marketplace_status_upgrade_migration_widens_existing_constraints():
    sql = read("infra/init/74_marketplace_status_constraints.sql")
    assert "DROP CONSTRAINT IF EXISTS tenant_entitlements_status_chk" in sql
    assert "DROP CONSTRAINT IF EXISTS cartridge_installations_status_chk" in sql
    for status in (
        "requested",
        "paused",
        "revoked",
        "expired",
        "suspended",
        "pending_connection",
    ):
        assert status in sql


def test_marketplace_service_separates_customer_request_from_admin_control():
    source = read("console/app/services/marketplace_service.py")
    assert "async def request_product" in source
    assert "async def approve_installation" in source
    assert "async def pause_installation" in source
    assert "async def revoke_installation" in source
    assert "async def reactivate_installation" in source
    assert "async def list_installation_access" in source
    assert "async def set_installation_user_access" in source
    assert "cartridge_activation_requested" in source
    assert "cartridge_revoked" in source
    assert "cartridge_user_access_updated" in source
    assert "p.status = 'active'" in source
    assert "p.status IN ('active', 'internal')" not in source
    assert "lower(COALESCE(p.metadata->>'internal_only', 'false')) NOT IN" in source
    assert "cartridge not allowed for this user" in source
    assert "user_cartridge_overrides" in source
    assert "uco.mode = 'deny'" in source


def test_marketplace_runtime_never_runs_schema_ddl():
    source = read("console/app/services/marketplace_service.py")
    assert "ALTER TABLE marketplace_orders" not in source
    assert "DROP CONSTRAINT" not in source
    assert "ADD CONSTRAINT" not in source
    assert "marketplace schema is not migrated" in source


def test_marketplace_admin_status_lock_targets_installation_only():
    source = read("console/app/services/marketplace_service.py")
    state_section = source.split("async def _set_installation_state", 1)[1].split(
        "def _decorate_installation", 1
    )[0]
    assert "FOR UPDATE OF ci" in state_section
    assert "FOR UPDATE\n" not in state_section
    assert "WHERE ci.id = $1" in state_section
    assert "UPDATE cartridge_installations" in state_section
    assert "WHERE id = $1" in state_section
    assert "ON CONFLICT (tenant_id, workspace_id, cartridge_id)" in state_section
    assert 'row["tenant_id"]' in state_section
    assert 'row["workspace_id"]' in state_section
    assert 'row["cartridge_id"]' in state_section


def test_marketplace_permissions_distinguish_request_from_admin():
    source = read("console/app/services/permissions.py")
    assert '"marketplace.request"' in source
    assert '"marketplace.admin"' in source
    assert (
        '"workspace_user": {"workspace.access", "apps.read", "marketplace.read", "marketplace.request"}'
        in source
    )
    assert (
        '"viewer": {"monitor.read", "workspace.access", "apps.read", "pipelines.read", "datasets.read", "cartridges.read", "marketplace.read", "copilot.use"}'
        in source
    )
    assert (
        '"user": {"monitor.read", "workspace.access", "apps.read", "marketplace.read"}'
        in source
    )
    assert '"tenant_admin": {' in source
    tenant_admin_section = source.split('"tenant_admin": {', 2)[2].split("},", 1)[0]
    assert '"iam.users.write"' in tenant_admin_section
    assert '"studio.read"' not in tenant_admin_section
    assert '"datasets.write"' not in tenant_admin_section


def test_marketplace_admin_and_retry_do_not_escalate_customer_access():
    main = console_route_source()
    service = read("console/app/services/marketplace_service.py")
    js = read("console/app/static/js/marketplace.js")
    retry_section = service.split("async def retry_installation", 1)[1].split(
        "async def list_installation_access", 1
    )[0]
    # Marketplace admin is enforced by the marketplace.admin PERMISSION, not a
    # hardcoded global role: the admin routes gate on require_permission and the
    # service resolves access through has_permission. owner/super_admin/admin
    # still pass because they carry marketplace.admin via ROLE_PERMISSIONS.
    assert 'require_permission("marketplace.admin")' in main
    assert 'permissions.has_permission(user, "marketplace.admin")' in service
    assert "in ADMIN_ROLES" not in service
    assert "admin approval required before retry" in retry_section
    assert "SET status = 'pending_connection'" in retry_section
    assert "UPDATE tenant_entitlements" not in retry_section
    assert "row.can_retry" in js
    assert "installation transition not allowed from current status" in service
    assert (
        'allowed_installation_statuses={"paused", "revoked", "expired", "suspended"}'
        in service
    )
    assert (
        "const reactivateStates = ['paused', 'revoked', 'expired', 'suspended']" in js
    )
    assert (
        "const approveStates = ['requested', 'pending_connection', 'waiting_credentials', 'failed', 'ready']"
        in js
    )
    assert 'href="/viewer/vault"' not in js


def test_marketplace_permissions_follow_selected_workspace_header():
    main = console_route_source()
    deps = read("console/app/dependencies.py")
    workspace_session = read("workspace/app/services/session.py")
    assert re.search(
        r"requested_workspace_id\s*=\s*\(\s*request\.headers\.get\(\"x-workspace-id\"\)\s*or\s*\"\"\s*\)\.strip\(\)\s*or\s*None",
        main,
    )
    assert '"workspace access forbidden"' in main
    assert re.search(
        r'"allowed_cartridges":\s*await\s+_workspace_cartridges\(\s*active_workspace\["workspace_id"\],\s*user_id=user\["id"\],?\s*\)',
        main,
    )
    assert re.search(
        r'"allowed_cartridges":\s*await\s+_workspace_cartridges\(\s*active_workspace\["workspace_id"\],\s*user_id=jwt_user\["id"\],?\s*\)',
        main,
    )
    assert "user_cartridge_overrides" in deps
    assert "uco.mode = 'deny'" in deps
    assert "user_cartridge_overrides" in workspace_session
    assert "uco.mode = 'deny'" in workspace_session


def test_marketplace_admin_user_access_api_is_server_side_and_audited():
    main = console_route_source()
    service = read("console/app/services/marketplace_service.py")
    js = read("console/app/static/js/marketplace.js")
    access_section = service.split("async def set_installation_user_access", 1)[
        1
    ].split("async def _set_installation_state", 1)[0]
    assert '"/api/admin/installations/{installation_id}/access"' in main
    assert (
        '"/api/admin/installations/{installation_id}/access/{target_user_id}"' in main
    )
    assert "Body(default_factory=dict)" in main
    assert "user is not assigned to this workspace" in access_section
    assert "DELETE FROM user_cartridge_overrides" in access_section
    assert (
        "ON CONFLICT (tenant_id, workspace_id, cartridge_id, user_id)" in access_section
    )
    assert "mode must be inherit or deny" in access_section
    assert "cartridge_user_access_updated" in access_section
    assert '"target_user_id"' in access_section
    assert 'value="allow"' not in js
    assert "Heredar workspace" in js
    assert "Bloquear" in js


def test_marketplace_rls_tables_use_scoped_db_context():
    source = read("console/app/services/marketplace_service.py")
    assert "from app.services.db_scope import scoped_db_for_user" in source
    assert "async with p.acquire() as conn" not in source

    scoped_functions = (
        "list_products",
        "request_product",
        "activate_product",
        "list_installations",
        "list_admin_installations",
        "get_admin_installation",
        "retry_installation",
        "list_installation_access",
        "set_installation_user_access",
        "_set_installation_state",
    )
    for fn in scoped_functions:
        section = source.split(f"async def {fn}", 1)[1].split("\n\nasync def ", 1)[0]
        assert "async with scoped_db_for_user(p, user)" in section, fn


def test_workspace_and_mcp_are_scoped_to_active_cartridge_entitlements():
    workspace = read("workspace/app/main.py")
    assistant = read("workspace/app/services/consumer_assistant.py")
    mcp = read("mcp-infra/app/main.py")
    refinement = read("refinement/app/main.py")
    security_context = read("console/app/services/security_context.py")
    cartridge_router = read("console/app/routers/cartridges.py")
    console_main = console_route_source()
    assert "_allowed_cartridge_set" in workspace
    assert (
        'return None if _is_admin_user(user) and not (user.get("active_workspace_id") or user.get("workspace_id")) else set()'
        in workspace
    )
    assert "if _is_admin_user(user):\n        return" not in workspace
    assert "cartridge_id = ANY($2::text[])" in workspace
    assert "WHERE cartridge_id = ANY($2::text[])" in workspace
    assert 'owner_id == user.get("id")' in workspace
    assert 'return owner_id == user.get("id")' in workspace
    assert "_app_allowed_for_user" in workspace
    assert "_visible_dataset_metadata" in workspace
    assert "WHERE d.name = $1" in workspace
    assert "d.workspace_id IS NOT NULL" in workspace
    assert "COALESCE(d.row_count, 0) > 0" in workspace
    assert "allowed_cartridges" in assistant
    assert 'unrestricted = "*" in allowed_cartridges' in assistant
    assert "or (admin and not explicit_scope)" not in assistant
    assert "unrestricted = admin and not explicit_scope" not in assistant
    assert 'if not unrestricted and "*" not in allowed' in assistant
    assert "explicit_scope = explicit_cartridges is not None" in security_context
    assert 'json=_payload("list_cartridges", {}, user)' in assistant
    assert (
        'json=_payload("cartridge_get_hints", {"cartridge_id": cartridge_id}, user)'
        in assistant
    )
    assert '"postgres_execute_query"' not in assistant
    assert '_mcp_payload("list_datasets"' in workspace
    assert '"get_schema"' in workspace
    assert "_user_with_dataset_scope(user, meta)" in workspace
    assert "_is_unscoped_admin_context" in mcp
    assert "if _is_unscoped_admin_context(ctx):\n        return" in mcp
    assert "if _is_admin_context(ctx):\n        return" not in mcp
    assert '"list_cartridges"' in mcp
    assert 'req.tool == "list_cartridges"' in mcp
    assert "_require_cartridge_visible" in cartridge_router
    assert "_require_cartridge_visible(user, cartridge)" in cartridge_router
    assert "_require_cartridge_visible(user, cartridge_id)" in console_main
    assert console_main.count("_require_cartridge_visible(user, cartridge_id)") >= 6
    assert "_is_unscoped_admin_security_context" in refinement
    assert "cartridge_id, datasets_used" in refinement
    assert 'f"cartridges/{cartridge}/"' in refinement

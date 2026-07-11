from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = (
    "hubspot",
    "banxico",
    "inegi",
    "replicon",
    "salesforce",
    "sec_edgar",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
)


def _read(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith(".py"):
        ast.parse(source)
    return source


def _function_args(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return {arg.arg for arg in node.args.args + node.args.kwonlyargs}
    raise AssertionError(f"function not found: {name}")


def _path_template(source: str) -> str:
    match = re.search(r'path_template:\s*"([^"]+)"', source)
    assert match, "connector.yaml must declare storage.path_template"
    return match.group(1)


def test_replicon_and_hubspot_declare_config_only_platform_runtime_contract():
    for cartridge in ("replicon", "hubspot"):
        source = _read(f"cartridges/{cartridge}/app/config/connector.yaml")
        assert "runtime:" in source
        assert "mode: config_only" in source
        assert "scope_source: signed_security_context" in source
        for forbidden in ("tenant_id", "workspace_id", "security_context"):
            assert forbidden in source
        for service in (
            "schema",
            "semantic",
            "watermarks",
            "jobs",
            "preview",
            "lineage",
            "catalog",
            "extraction",
            "rag",
            "control_room",
        ):
            assert f"- {service}" in source


def test_cartridge_connector_configs_declare_scoped_storage_prefixes():
    for cartridge in CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/app/config/connector.yaml")
        template = _path_template(source)
        assert template.startswith(f"raw/{cartridge}/")
        assert "/tenant_id={tenant_id}/workspace_id={workspace_id}/" in template
        assert "/load_date={load_date}/batch_id={run_id}/" in template


def test_collaborator_iam_branch_is_not_merged_directly():
    main = _read("console/app/main.py")
    sidebar = _read("console-next/src/components/AppSidebar.tsx")

    assert '@app.post("/api/admin/tenants"' not in main
    assert '@app.post("/api/admin/workspaces"' not in main
    assert 'href: "/admin/tenants"' not in sidebar
    assert 'href: "/admin/workspaces"' not in sidebar
    assert not (ROOT / "cartridges/platform/dags").exists()


def test_workspace_context_is_backend_driven_and_sent_as_header():
    access = _read("console/app/main.py")
    api = _read("console-next/src/lib/api.ts")
    sidebar = _read("console-next/src/components/AppSidebar.tsx")

    assert '"workspaces": switchable_workspaces' in access
    assert "ACTIVE_WORKSPACE_COOKIE" in api
    assert 'headers.set("X-Workspace-Id", activeWorkspaceId)' in api
    assert "WorkspaceSwitcher" in sidebar


def test_semantic_and_control_room_use_config_only_entitlements_not_vault_only_connections():
    main = _read("console/app/main.py")
    control_room = _read("console/app/services/control_room/api.py")
    control_room_page = _read("console-next/src/app/(shell)/control-room/page.tsx")

    assert "def _resolve_scoped_config_cartridge(" in main
    semantic_section = main.split('@app.get("/api/semantic"', 1)[1].split("# ── Data Catalog API", 1)[0]
    assert "cartridge, _active = await _resolve_scoped_operation_cartridge(" in semantic_section
    assert "must not require an active Vault" in main

    installation_filter = control_room.split("async def _filter_installations_by_scoped_connections", 1)[1].split("@_bind_to_core", 1)[0]
    assert "connected = [row for row in candidates if row.get(\"connection_count\")]" in installation_filter
    assert "return connected or candidates" in installation_filter

    assert "successFactorsAvailable" in control_room_page
    assert "clearSuccessFactorsState" in control_room_page
    assert "item.connector_id === \"sap_successfactors\"" in control_room_page


def test_users_and_vault_are_workspace_scoped_in_ui():
    users_table = _read("console-next/src/components/operations/UsersTable.tsx")
    create_user = _read("console-next/src/components/operations/CreateUserForm.tsx")
    vault = _read("console-next/src/components/operations/VaultConnectionsTable.tsx")

    assert "Filtrar por workspace" in users_table
    assert "Workspace destino" in create_user
    assert "useCartridgeList" in vault
    assert "const CARTRIDGES =" not in vault


def test_mcp_generic_cartridge_tools_do_not_expose_client_owned_scope_args():
    gateway = _read("mcp-infra/app/main.py")
    tools = _read("mcp-infra/app/tools/cartridges.py")

    assert "def _reject_client_owned_scope_args(" in gateway
    assert "_reject_client_owned_scope_args(args)" in gateway
    assert '"backend-owned arg is not allowed' in gateway
    assert "_CARTRIDGE_CONTEXT_TOOLS" in gateway
    assert 'args["security_context"] = ctx' in gateway
    for public_arg in (
        '"tenant_id": {"type": "string"}',
        '"workspace_id": {"type": "string"}',
        '"security_context": {"type": "object"}',
    ):
        assert public_arg not in tools

    for function in (
        "cartridge_get_semantic",
        "cartridge_search_term",
        "cartridge_get_manifest",
        "cartridge_get_hints",
        "cartridge_sync_semantic_to_rag",
        "cartridge_list_entities",
        "cartridge_get_schema",
        "cartridge_preview",
        "cartridge_list_jobs",
        "cartridge_list_kbs",
        "cartridge_extract",
        "cartridge_extract_all",
        "cartridge_run_kb",
        "cartridge_query_kb",
    ):
        args = _function_args(tools, function)
        assert "security_context" in args
        assert "tenant_id" not in args
        assert "workspace_id" not in args

    assert "_scoped_rag_source_name(" in tools
    assert "connections = []" in tools
    assert "if not workspace_scoped:" in tools
    assert "resolved = _scope_cartridge_sql(sql, cartridge_id, security_context)" in tools

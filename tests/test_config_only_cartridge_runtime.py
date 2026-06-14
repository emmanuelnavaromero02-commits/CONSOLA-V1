from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = (
    "hubspot",
    "replicon",
    "salesforce",
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
    for public_arg in (
        '"tenant_id": {"type": "string"}',
        '"workspace_id": {"type": "string"}',
        '"security_context": {"type": "object"}',
    ):
        assert public_arg not in tools

    for function in (
        "cartridge_sync_semantic_to_rag",
        "cartridge_list_entities",
        "cartridge_preview",
        "cartridge_extract",
        "cartridge_extract_all",
        "cartridge_run_kb",
    ):
        args = _function_args(tools, function)
        assert "security_context" in args
        assert "tenant_id" not in args
        assert "workspace_id" not in args

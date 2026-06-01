from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(source)
    return source


def _function_args(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return {arg.arg for arg in node.args.args + node.args.kwonlyargs}
    raise AssertionError(f"function not found: {name}")


def test_console_entity_run_forwards_backend_security_context_to_cartridge_skills():
    source = _read("console/app/routers/cartridges.py")

    assert "security_context = build_security_context(user)" in source
    assert 'json={"security_context": security_context}' in source


def test_replicon_and_hubspot_skills_preserve_forwarded_workspace_scope():
    for cartridge in ("replicon", "hubspot"):
        source = _read(f"cartridges/{cartridge}/app/api/routes_skills.py")

        assert "Body(None)" in source
        assert "def _security_context(" in source
        assert "token = set_security_context(ctx)" in source
        assert 'scoped_config = {**config, "security_context": ctx}' in source
        assert "reset_security_context(token)" in source


def test_cartridge_job_runners_pass_scope_to_airflow_conf():
    for cartridge in ("replicon", "hubspot"):
        source = _read(f"cartridges/{cartridge}/app/core/job_runner.py")

        assert 'conf["security_context"] = security_context' in source
        assert 'conf["tenant_id"] = security_context["tenant_id"]' in source
        assert 'conf["workspace_id"] = security_context["workspace_id"]' in source


def test_airflow_dags_forward_scope_to_raw_writes_and_skill_calls():
    replicon = _read("cartridges/replicon/dags/replicon_extract.py")
    hubspot = _read("cartridges/hubspot/dags/hubspot_extract.py")

    assert "def _scope_prefix(" in replicon
    assert 'return f"tenant_id={tenant}/workspace_id={workspace}/"' in replicon
    assert "_upload_parquet(df, entity, run_id, tenant_id, workspace_id)" in replicon
    assert 'key = f"raw/replicon/{entity}/{scope}load_date=' in replicon
    assert "skill_body = {" in hubspot
    assert 'for key in ("tenant_id", "workspace_id", "security_context")' in hubspot
    assert "json=skill_body" in hubspot


def test_mcp_infra_injects_trusted_scope_before_cartridge_execution():
    source = _read("mcp-infra/app/main.py")
    tools = _read("mcp-infra/app/tools/cartridges.py")

    assert "def _inject_cartridge_execution_scope(" in source
    assert 'args["security_context"] = ctx' in source
    assert 'args["tenant_id"] = tenant_id' in source
    assert 'args["workspace_id"] = workspace_id' in source
    assert "_inject_cartridge_execution_scope(ctx, args)" in source
    assert 'conf["security_context"] = ctx' in source
    assert "def _attach_security_scope(" in tools
    assert '"security_context": {"type": "object"}' in tools
    assert "security_context: dict[str, Any] | None = None" in tools
    assert "_scoped_object_prefix(" in tools
    assert "_scoped_rag_source_name(" in tools

    for fn_name in (
        "cartridge_sync_semantic_to_rag",
        "cartridge_extract",
        "cartridge_extract_all",
        "cartridge_run_kb",
    ):
        assert {"tenant_id", "workspace_id", "security_context"} <= _function_args(tools, fn_name)


def test_mcp_admin_sql_sensitive_table_denylist_covers_tenant_and_vault_tables():
    source = _read("mcp-infra/app/main.py")

    for table in (
        "tenants",
        "workspaces",
        "roles",
        "user_workspace_roles",
        "decisions",
        "decision_actions",
        "vault_access_log",
        "vault_entries",
    ):
        assert f'"{table}"' in source

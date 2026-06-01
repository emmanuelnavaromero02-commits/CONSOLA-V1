from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_MAIN = ROOT / "mcp-infra" / "app" / "main.py"
MINIO_TOOLS = ROOT / "mcp-infra" / "app" / "tools" / "minio.py"
REFINEMENT_MAIN = ROOT / "refinement" / "app" / "main.py"
MCP_REGISTRY = ROOT / "console" / "app" / "services" / "mcp_registry.py"


def test_mcp_invoke_enforces_trusted_security_context_before_data_tools():
    source = MCP_MAIN.read_text(encoding="utf-8")
    ast.parse(source)

    assert "trusted security_context required" in source
    assert "_enforce_data_scope(req, internal_service)" in source
    assert '"postgres_execute_query"' in source
    assert '"minio_list_objects"' in source
    assert '"cartridge_preview"' in source
    assert '"cartridge_query_kb"' in source
    assert "object prefix is required" in source
    assert "main database access requires explicit unscoped admin context" in source
    assert "cartridge SQL must stay inside its cartridge prefix" in source
    assert "sensitive internal tables are not readable through MCP" in source
    assert "_AGENT_READ_TOOLS" in source
    assert "_AGENT_WRITE_TOOLS" in source
    assert "_AGENT_DESTRUCTIVE_TOOLS" in source
    assert "scheduled agents cannot manage agents" in source
    assert "agent management requires admin context" in source
    assert "_VAULT_READ_TOOLS" in source
    assert "_VAULT_WRITE_TOOLS" in source
    assert "vault writes require admin context" in source
    assert "vault.connections.read" in source
    assert "vault.connections.write" in source
    assert "_SUPERSET_TOOLS" in source
    assert "superset tools require admin studio.write context" in source
    assert "_validate_airflow_trigger_scope" in source
    assert "_require_dag_registered_for_cartridge" in source
    assert "DAG is not registered for cartridge" in source
    assert "shared DAG trigger requires cartridge_id outside admin context" in source
    assert "DAG trigger requires cartridge_id outside admin context" in source
    trigger_block = source.split("def _validate_airflow_trigger_scope", 1)[1].split("def _extract_s3_keys", 1)[0]
    assert "startswith(tuple" not in trigger_block


def test_refinement_preview_transform_rejects_unscoped_duckdb_readers():
    source = REFINEMENT_MAIN.read_text(encoding="utf-8")
    ast.parse(source)

    assert "_SQL_READER_CALL_RE" in source
    assert "_SCOPED_READER_RE" in source
    assert "_SQL_STORAGE_LITERAL_RE" in source
    assert "_DIRECT_STORAGE_SCAN_RE" in source
    assert "_PGGOLD_SCHEMA_TABLE_RE" in source
    assert 'path.replace("s3://{bucket}/", f"s3://{engine.minio_bucket}/", 1)' in source
    assert "sql = _strip_sql_comments(sql or \"\")" in source
    assert "len(reader_calls) != len(direct_readers)" in source
    assert "SQL readers must use a direct string literal path" in source
    assert "pgdb schema is not readable through refinement" in source
    assert "_body_from_security_header" in source
    assert "_require_dataset_scope(_body_from_security_header" in source
    assert "pggold table is not registered as an allowed dataset" in source
    assert "existing = store.get_dataset(args[\"name\"])" in source
    assert "_require_dataset_scope(body, existing, \"datasets.write\")" in source
    assert "SQL storage bucket not allowed" in source
    assert 'return {"sources": [source for source in engine.list_sources() if _prefix_allowed(sec, source)]}' in source
    assert "_require_sql_storage_scope(" in source
    assert "allow_registered_dataset_paths=True" in source
    assert "_storage_path_matches_declared_source" in source
    assert "_storage_path_matches_registered_dataset" in source
    assert "sec = _security_context(body)" in source


def test_mcp_infra_rag_and_cartridge_sql_are_scoped():
    source = MCP_MAIN.read_text(encoding="utf-8")
    ast.parse(source)

    assert "def _allowed_prefix_matches" in source
    assert "len(prefix.split(\"/\")) < 2" in source
    assert "_validate_cartridge_query_sql" in source
    assert "_postgres_mentioned_tables" in source
    assert "_DIRECT_STORAGE_SCAN_RE" in source
    assert "cartridge SQL must read only direct s3:// file literals" in source
    assert "cartridge SQL cannot read service database schemas" in source
    assert 'if tool == "cartridge_preview":' in source
    assert "cartridge preview requires tenant/workspace scope" in source
    assert "_inject_cartridge_execution_scope(ctx, args)" in source
    assert "_has_invalid_scoped_storage_path" in source
    assert 'if tool == "cartridge_query_kb":' in source
    assert 'tool == "cartridge_query_kb" and not _is_unscoped_admin_context' not in source
    assert "RAG source is outside caller scope" in source
    assert "source = next((s for s in sources" in source


def test_minio_sample_rows_are_capped():
    source = MINIO_TOOLS.read_text(encoding="utf-8")
    ast.parse(source)

    assert "n = min(max(int(n or 10), 1), 100)" in source
    assert "_safe_filename" in source
    assert "MAX_SPEC_BYTES" in source


def test_console_registry_scopes_direct_cartridge_mcp_calls():
    source = MCP_REGISTRY.read_text(encoding="utf-8")
    ast.parse(source)

    assert "SELECT url, category FROM mcp_servers" in source
    assert "_enforce_outbound_scope" in source
    assert "category != \"cartridge\"" in source
    assert "\"query_kb\"" in source
    assert "trusted security_context required" in source
    assert "cartridge SQL cannot read service database schemas" in source
    assert "_DIRECT_STORAGE_SCAN_RE" in source
    assert "_has_invalid_scoped_storage_path" in source
    assert 'if tool == "query_kb":' in source
    assert 'tool == "query_kb" and not _is_admin_context' not in source
    assert "except HTTPException" in source
    assert "MCP transport failed" in source
    assert "MCP invoke failed" in source
    assert 'return {"error": str(exc)}' not in source

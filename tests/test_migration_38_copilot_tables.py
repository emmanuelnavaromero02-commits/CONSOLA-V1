from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "infra" / "init" / "38_copilot_conversations.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_conversations_table_exists():
    assert MIGRATION.exists(), "migration 38 not found"
    sql = _sql()
    assert re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+conversations\b", sql, re.IGNORECASE)


def test_conversations_has_workspace_isolation():
    sql = _sql()
    assert "REFERENCES workspaces(id)" in sql, "workspace_id must FK to workspaces(id)"
    assert "REFERENCES users(id)" in sql, "user_id must FK to users(id)"


def test_conversation_messages_table_exists():
    sql = _sql()
    assert re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+conversation_messages\b", sql, re.IGNORECASE)


def test_messages_role_constraint():
    sql = _sql()
    assert re.search(
        r"CHECK\s*\(\s*role\s+IN\s*\(\s*'user'\s*,\s*'assistant'\s*,\s*'tool'\s*,\s*'system'\s*\)\s*\)",
        sql,
        re.IGNORECASE,
    ), "role CHECK constraint not found or wrong values"


def test_indices_present():
    sql = _sql()
    assert "idx_conversations_user_workspace" in sql
    assert "idx_messages_conversation" in sql


def test_cartridge_roles_denied_on_conversations():
    sql = _sql()
    for role in (
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_replicon",
        "omega_mcp_infra",
        "omega_airflow_dag",
    ):
        assert role in sql, f"cartridge role {role} missing from REVOKE block"
    assert re.search(r"REVOKE\s+ALL\s+ON\s+conversations\b", sql, re.IGNORECASE)
    assert re.search(r"REVOKE\s+ALL\s+ON\s+conversation_messages\b", sql, re.IGNORECASE)


def test_omega_console_has_grants_on_conversations():
    sql = _sql()
    assert re.search(
        r"GRANT\s+SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+ON\s+conversations\s+TO\s+omega_console",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        r"GRANT\s+SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+ON\s+conversation_messages\s+TO\s+omega_console",
        sql,
        re.IGNORECASE,
    )


def test_idempotency_guards():
    sql = _sql()
    assert sql.count("IF NOT EXISTS") >= 4
    assert "IF EXISTS (SELECT 1 FROM pg_roles" in sql

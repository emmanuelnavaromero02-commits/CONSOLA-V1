"""v1.44.5 — mcp_servers.tool_count registry contract."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/55_mcp_servers_tool_count.sql"
SCHEMA = REPO / "infra/init/00_schema.sql"
REGISTRY = REPO / "console/app/services/mcp_registry.py"


def test_migration_55_exists_and_self_registers():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS tool_count INT NOT NULL DEFAULT 0" in src
    assert "jsonb_array_length(tools)" in src
    assert "INSERT INTO schema_migrations" in src
    assert "'55_mcp_servers_tool_count.sql'" in src
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_base_schema_has_tool_count_for_fresh_installs():
    src = SCHEMA.read_text(encoding="utf-8")
    assert "tool_count  INT NOT NULL DEFAULT 0" in src


def test_registry_persists_tool_count_on_register_and_refresh():
    src = REGISTRY.read_text(encoding="utf-8")
    assert "tool_count = len(tools)" in src
    assert "INSERT INTO mcp_servers" in src
    assert "tool_count" in src
    assert '"tool_count": tool_count' in src


def _psql(sql: str) -> str:
    cmd = [
        "docker", "exec", "mode_postgres", "psql",
        "-U", "postgres", "-d", "modecissions", "-At", "-c", sql,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=10, check=False)
    if proc.returncode != 0:
        pytest.skip(f"live postgres stack not reachable: {proc.stderr.strip()}")
    return proc.stdout.strip()


def test_live_mcp_servers_tool_count_column_if_stack_is_up():
    column = _psql(
        "SELECT data_type || ':' || is_nullable || ':' || column_default "
        "FROM information_schema.columns "
        "WHERE table_name='mcp_servers' AND column_name='tool_count';"
    )
    if not column:
        pytest.skip("live stack has not applied migration 55 yet")
    assert column.startswith("integer:NO:0")

    rows = _psql(
        "SELECT count(*) FROM mcp_servers "
        "WHERE category='cartridge' AND healthy=true AND tool_count > 0;"
    )
    assert int(rows or "0") >= 4

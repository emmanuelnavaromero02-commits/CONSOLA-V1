from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


@pytest.mark.parametrize("cartridge", ["replicon", "hubspot"])
def test_query_kb_blocks_file_read_before_duckdb(cartridge, monkeypatch):
    load_cartridge_app(cartridge)
    from app import mcp_server

    def fail_get_connection():
        raise AssertionError("DuckDB should not be opened for blocked SQL")

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", fail_get_connection)
    result = mcp_server.query_kb("SELECT * FROM read_parquet('file:///etc/passwd')")

    assert result["error"] == "sql_blocked"
    assert "S3 prefix" in result["reason"]


@pytest.mark.parametrize(
    "cartridge,entity",
    [("replicon", "TimeEntry"), ("hubspot", "deals")],
)
def test_query_kb_error_response_does_not_leak_sql_or_paths(cartridge, entity, monkeypatch):
    load_cartridge_app(cartridge)
    from app import mcp_server

    class LeakyConnection:
        def execute(self, sql: str):
            raise RuntimeError(f"boom while reading {sql}")

        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", lambda: LeakyConnection())

    result = mcp_server.query_kb(
        f"SELECT * FROM read_parquet('s3://{{bucket}}/raw/{cartridge}/{entity}/*.parquet')"
    )

    rendered = repr(result)
    assert result == {"error": "query_failed", "reason": "DuckDB query failed"}
    assert "SELECT" not in rendered
    assert "s3://lakehouse" not in rendered


@pytest.mark.parametrize("cartridge", ["replicon", "hubspot"])
def test_custom_sql_tool_blocks_invalid_sql(cartridge, monkeypatch):
    load_cartridge_app(cartridge)
    from app import mcp_server

    registered = []
    monkeypatch.setattr(mcp_server.mcp, "add_tool", lambda fn: registered.append(fn))

    mcp_server._make_sql_tool(
        f"unsafe_{cartridge}_tool",
        "Unsafe tool",
        "SELECT * FROM read_parquet('file:///etc/passwd')",
    )

    assert len(registered) == 1
    result = registered[0]()
    assert result["error"] == "sql_blocked"
    assert "S3 prefix" in result["reason"]


@pytest.mark.parametrize("cartridge", ["replicon", "hubspot"])
def test_preview_rejects_identifier_injection_before_duckdb(cartridge, monkeypatch):
    load_cartridge_app(cartridge)
    from app import mcp_server

    def fail_get_connection():
        raise AssertionError("DuckDB should not be opened for invalid entity")

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", fail_get_connection)
    result = mcp_server.preview("TimeEntry'); DROP TABLE x; --", limit=20)

    assert result["error"] == "invalid_argument"
    assert "Invalid entity" in result["reason"]

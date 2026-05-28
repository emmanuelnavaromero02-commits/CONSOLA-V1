from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())


PREFIX = "s3://lakehouse/raw/sap_hcm/"
PREFIXES = (
    PREFIX,
    "s3://lakehouse/silver/sap_hcm/",
    "s3://lakehouse/gold/sap_hcm/",
)


def _validate_kb_sql(sql: str):
    from app.core.sql_guard import validate_kb_sql

    return validate_kb_sql(sql, PREFIXES)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 AS ok",
        "WITH x AS (SELECT 1 AS ok) SELECT * FROM x",
        "SELECT * FROM read_parquet('s3://lakehouse/raw/sap_hcm/Employee/*.parquet')",
        'SELECT * FROM read_parquet("s3://lakehouse/raw/sap_hcm/TimeEntry/**/*.parquet")',
        "SELECT * FROM read_parquet('s3://lakehouse/silver/sap_hcm/kb_headcount/*.parquet')",
        "SELECT * FROM read_parquet('s3://lakehouse/gold/sap_hcm/headcount_by_department/*.parquet')",
    ],
)
def test_validate_kb_sql_allows_safe_reads(sql):
    assert _validate_kb_sql(sql) == (True, None)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("", "empty"),
        ("DROP TABLE x", "SELECT/WITH"),
        ("ATTACH 'foo.db' AS x", "SELECT/WITH"),
        ("SELECT 1; ATTACH 'foo.db' AS x", "Multiple statements"),
        ("SELECT * FROM x -- hide", "comments"),
        ("SELECT * FROM x /* hide */", "comments"),
        ("SELECT * FROM read_parquet('file:///etc/passwd')", "S3 prefixes"),
        ("SELECT * FROM read_csv('file:///etc/passwd')", "S3 prefixes"),
        ("SELECT * FROM read_parquet('/etc/passwd')", "S3 prefixes"),
        ("SELECT * FROM read_parquet('../secret.parquet')", "S3 prefixes"),
        ("SELECT * FROM read_parquet('s3://lakehouse/raw/sap_hcm/../secret.parquet')", "traversal"),
        ("SELECT * FROM read_parquet('s3://other/raw/sap_hcm/x.parquet')", "path must start"),
        ("SELECT * FROM read_parquet('s3://lakehouse/raw/sap_s4hana/x.parquet')", "path must start"),
        ("SELECT * FROM read_parquet(['file:///etc/passwd'])", "direct string literal"),
        ("SELECT * FROM parquet_scan('/etc/passwd')", "parquet_scan"),
        ("SELECT * FROM read_csv_auto('/etc/passwd')", "read_csv_auto"),
        ("SELECT * FROM read_json_auto('/etc/passwd')", "read_json_auto"),
        ("SELECT * FROM read_text('/etc/passwd')", "read_text"),
        ("SELECT * FROM x WHERE a = 1 UPDATE y SET a=2", "UPDATE"),
        ("SELECT * FROM x DELETE FROM y", "DELETE"),
        ("SELECT * FROM x PRAGMA database_list", "PRAGMA"),
        ("SELECT * FROM x COPY TO 'x'", "COPY"),
        ("SELECT * FROM x EXPORT DATABASE 'out'", "EXPORT"),
        ("SELECT * FROM x INSTALL httpfs", "INSTALL"),
        ("SELECT * FROM x LOAD httpfs", "LOAD"),
        ("SELECT * FROM x CALL system('id')", "CALL"),
        ("SELECT * FROM x SET enable_progress_bar=true", "SET"),
        ("SELECT * FROM x LIMIT -1", "LIMIT"),
        ("SELECT * FROM x LIMIT 999999", "LIMIT exceeds"),
        ("SELECT * FROM x\x00", "NUL"),
    ],
)
def test_validate_kb_sql_blocks_dangerous_queries(sql, expected):
    ok, reason = _validate_kb_sql(sql)
    assert ok is False
    assert reason is not None
    assert expected in reason


def test_query_kb_returns_sql_blocked_before_duckdb(monkeypatch):
    from app import mcp_server

    def fail_get_connection():
        raise AssertionError("DuckDB should not be opened for blocked SQL")

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", fail_get_connection)
    result = mcp_server.query_kb("SELECT * FROM read_parquet('file:///etc/passwd')")
    assert result["error"] == "sql_blocked"
    assert "S3 prefix" in result["reason"]


def test_query_kb_rejects_invalid_limit_before_duckdb(monkeypatch):
    from app import mcp_server

    def fail_get_connection():
        raise AssertionError("DuckDB should not be opened for invalid LIMIT")

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", fail_get_connection)
    result = mcp_server.query_kb("SELECT 1 AS ok", limit=-1)
    assert result["error"] == "invalid_limit"
    assert "positive integer" in result["reason"]


def test_query_kb_caps_huge_limit_argument(monkeypatch):
    from app import mcp_server

    class FakeResult:
        description = [("ok",)]

        def fetchall(self):
            return [(1,)]

    class FakeConnection:
        def __init__(self):
            self.executed: list[str] = []

        def execute(self, sql: str):
            self.executed.append(sql)
            return FakeResult()

        def close(self):
            pass

    conn = FakeConnection()
    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", lambda: conn)

    result = mcp_server.query_kb("SELECT 1 AS ok", limit=999999)

    assert result["count"] == 1
    assert conn.executed == ["SELECT * FROM (SELECT 1 AS ok) _q LIMIT 5000"]


def test_query_kb_error_response_does_not_leak_resolved_sql_or_paths(monkeypatch):
    from app import mcp_server

    class LeakyConnection:
        def execute(self, sql: str):
            raise RuntimeError(f"boom while reading {sql} from s3://lakehouse/raw/sap_hcm/User/x.parquet")

        def close(self):
            pass

    monkeypatch.setattr(mcp_server, "_get_duckdb_connection", lambda: LeakyConnection())

    result = mcp_server.query_kb("SELECT * FROM read_parquet('s3://{bucket}/raw/sap_hcm/User/*.parquet')")

    rendered = repr(result)
    assert result == {"error": "query_failed", "reason": "DuckDB query failed"}
    assert "SELECT" not in rendered
    assert "s3://lakehouse" not in rendered


def test_custom_sql_tool_blocks_invalid_sql(monkeypatch):
    from app import mcp_server

    registered = []
    monkeypatch.setattr(mcp_server.mcp, "add_tool", lambda fn: registered.append(fn))

    mcp_server._make_sql_tool(
        "unsafe_hcm_tool",
        "Unsafe tool",
        "SELECT * FROM read_parquet('file:///etc/passwd')",
    )

    assert len(registered) == 1
    result = registered[0]()
    assert result["error"] == "sql_blocked"
    assert "S3 prefix" in result["reason"]


def test_existing_hcm_kb_sql_passes_guard():
    repo_root = Path(__file__).resolve().parents[3]
    kb_path = repo_root / "cartridges" / "sap_hcm" / "app" / "config" / "knowledge_bits.yaml"
    kbs = yaml.safe_load(kb_path.read_text(encoding="utf-8")).get("knowledge_bits", [])
    assert kbs

    for kb in kbs:
        resolved = kb["sql"].replace("{bucket}", "lakehouse")
        ok, reason = _validate_kb_sql(resolved)
        assert ok, f"{kb['id']}: {reason}"


def test_limit_detection_ignores_strings():
    from app.core.sql_guard import has_limit_clause

    assert has_limit_clause("SELECT * FROM x LIMIT 10") is True
    assert has_limit_clause("SELECT * FROM read_parquet('s3://lakehouse/raw/sap_hcm/unlimited.parquet')") is False

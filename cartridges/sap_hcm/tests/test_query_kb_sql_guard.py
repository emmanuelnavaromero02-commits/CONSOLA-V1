from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.core.sql_guard import validate_kb_sql


PREFIX = "s3://lakehouse/raw/sap_hcm/"
PREFIXES = (PREFIX, "s3://lakehouse/silver/sap_hcm/")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 AS ok",
        "WITH x AS (SELECT 1 AS ok) SELECT * FROM x",
        "SELECT * FROM read_parquet('s3://lakehouse/raw/sap_hcm/Employee/*.parquet')",
        'SELECT * FROM read_parquet("s3://lakehouse/raw/sap_hcm/TimeEntry/**/*.parquet")',
        "SELECT * FROM read_parquet('s3://lakehouse/silver/sap_hcm/kb_headcount/*.parquet')",
    ],
)
def test_validate_kb_sql_allows_safe_reads(sql):
    assert validate_kb_sql(sql, PREFIXES) == (True, None)


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
        ("SELECT * FROM x PRAGMA database_list", "PRAGMA"),
        ("SELECT * FROM x COPY TO 'x'", "COPY"),
        ("SELECT * FROM x INSTALL httpfs", "INSTALL"),
        ("SELECT * FROM x LOAD httpfs", "LOAD"),
        ("SELECT * FROM x SET enable_progress_bar=true", "SET"),
        ("SELECT * FROM x\x00", "NUL"),
    ],
)
def test_validate_kb_sql_blocks_dangerous_queries(sql, expected):
    ok, reason = validate_kb_sql(sql, PREFIX)
    assert ok is False
    assert reason is not None
    assert expected in reason


def test_query_kb_returns_sql_blocked_before_duckdb():
    from app.mcp_server import query_kb

    result = query_kb("SELECT * FROM read_parquet('file:///etc/passwd')")
    assert result["error"] == "sql_blocked"
    assert "S3 prefix" in result["reason"]


def test_limit_detection_ignores_strings():
    from app.core.sql_guard import has_limit_clause

    assert has_limit_clause("SELECT * FROM x LIMIT 10") is True
    assert has_limit_clause("SELECT * FROM read_parquet('s3://lakehouse/raw/sap_hcm/unlimited.parquet')") is False

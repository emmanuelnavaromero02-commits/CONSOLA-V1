"""Differential parse-only gate; invoked explicitly by the PostgreSQL CI job."""

from __future__ import annotations

import os

import pytest

from app.services.public_sql_sensitivity import contains_public_sql
from public_sql_parse_oracles import (
    ParseResult,
    PostgreSQLOracleConfig,
    duckdb_parse_only,
    postgresql_parse_only,
)


SQL_STATEMENTS = (
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
    "SHOW ALL TABLES",
    "DESC payroll",
    "FROM payroll employees",
    "USE memory.main",
    "CALL refresh_payroll()",
    "SET search_path TO private",
    "COPY payroll TO STDOUT",
    "GRANT SELECT ON payroll TO analyst",
    "WITH p AS (VALUES (1)) SELECT * FROM p",
    "SHOW TRANSACTION ISOLATION LEVEL",
    "PIVOT cities USING sum(population)",
    "PIVOT cities GROUP BY country",
    "PIVOT_WIDER cities USING sum(population)",
    "LOCK payroll",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
    "SET XML OPTION DOCUMENT",
    "DO LANGUAGE plpgsql $$BEGIN NULL; END$$",
    "SECURITY LABEL FOR selinux ON ROLE analyst IS 'label'",
    "SET LOCAL ROLE analyst",
    "SET SESSION ROLE analyst",
    "SET SESSION TIME ZONE 'UTC'",
    "SET LOCAL XML OPTION DOCUMENT",
    "COPY BINARY payroll TO STDOUT",
)
KEYWORD_INITIAL_BUSINESS_COPY = (
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
)
SAME_HEAD_PAIRS = (
    ("CALL center()", "Call center roster"),
    ("SET core = values", "Set of core values"),
    ("GRANT portfolio TO reviewer", "Grant Portfolio Review"),
    ("COPY contracts TO STDOUT", "Copy of the signed contract"),
)
EMBEDDED_SQL_PREFIXES = (
    ("Show me the Q4 report", "SHOW me"),
    ("Describe the onboarding process", "DESCRIBE the"),
    ("Use of force policy", "USE of"),
    ("Set goals to improve performance", "SET goals TO improve"),
    ("Set expectations to align teams", "SET expectations TO align"),
    ("Business note. FROM range(10)", "FROM range(10)"),
    ("note TABLE employees please", "TABLE employees"),
    ("note SHOW ALL TABLES please", "SHOW ALL TABLES"),
    ("note SUMMARIZE payroll_internal please", "SUMMARIZE payroll_internal"),
    ("x PIVOT t ON x", "PIVOT t ON x"),
)


def _postgres_config() -> PostgreSQLOracleConfig:
    host = os.environ.get("PUBLIC_SQL_POSTGRES_HOST")
    if not host:
        raise AssertionError("PUBLIC_SQL_POSTGRES_HOST is required by the oracle gate")
    return PostgreSQLOracleConfig(
        host=host,
        port=int(os.environ.get("PUBLIC_SQL_POSTGRES_PORT", "55432")),
        user=os.environ.get("PUBLIC_SQL_POSTGRES_USER", "postgres"),
        database=os.environ.get("PUBLIC_SQL_POSTGRES_DB", "sql_oracle"),
    )


@pytest.mark.parametrize("statement", SQL_STATEMENTS)
def test_real_parser_union_accepts_every_sql_control(statement: str) -> None:
    outcomes = {
        duckdb_parse_only(statement),
        postgresql_parse_only(statement, _postgres_config()),
    }

    assert outcomes & {ParseResult.ACCEPTED, ParseResult.ACCEPTED_MULTIPLE}
    assert contains_public_sql(statement)


@pytest.mark.parametrize("business_copy", KEYWORD_INITIAL_BUSINESS_COPY)
def test_both_real_parsers_reject_keyword_initial_business_copy(
    business_copy: str,
) -> None:
    assert duckdb_parse_only(business_copy) is ParseResult.REJECTED
    assert (
        postgresql_parse_only(business_copy, _postgres_config()) is ParseResult.REJECTED
    )
    assert not contains_public_sql(business_copy)


@pytest.mark.parametrize(("statement", "business_copy"), SAME_HEAD_PAIRS)
def test_generated_same_head_pair_tracks_real_parser_union(
    statement: str,
    business_copy: str,
) -> None:
    statement_results = {
        duckdb_parse_only(statement),
        postgresql_parse_only(statement, _postgres_config()),
    }
    assert statement_results & {ParseResult.ACCEPTED, ParseResult.ACCEPTED_MULTIPLE}
    assert duckdb_parse_only(business_copy) is ParseResult.REJECTED
    assert (
        postgresql_parse_only(business_copy, _postgres_config()) is ParseResult.REJECTED
    )


@pytest.mark.parametrize(("raw", "sql_prefix"), EMBEDDED_SQL_PREFIXES)
def test_real_parser_accepts_embedded_prefix_and_runtime_blocks_full_raw(
    raw: str,
    sql_prefix: str,
) -> None:
    results = {
        duckdb_parse_only(sql_prefix),
        postgresql_parse_only(sql_prefix, _postgres_config()),
    }
    assert results & {ParseResult.ACCEPTED, ParseResult.ACCEPTED_MULTIPLE}
    assert contains_public_sql(raw)


def test_both_real_parsers_consume_and_report_multiple_statements() -> None:
    statements = "SELECT 1; SELECT 2"

    assert duckdb_parse_only(statements) is ParseResult.ACCEPTED_MULTIPLE
    assert (
        postgresql_parse_only(statements, _postgres_config())
        is ParseResult.ACCEPTED_MULTIPLE
    )
    assert contains_public_sql(statements)

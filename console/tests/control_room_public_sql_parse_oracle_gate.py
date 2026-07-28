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
)
KEYWORD_INITIAL_BUSINESS_COPY = (
    "Show me the Q4 report",
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
    "Describe the onboarding process",
    "Use of force policy",
)
SAME_HEAD_PAIRS = (
    ("SHOW Solutions", "Show me the Q4 report"),
    ("CALL center()", "Call center roster"),
    ("SET core = values", "Set of core values"),
    ("GRANT portfolio TO reviewer", "Grant Portfolio Review"),
    ("COPY contracts TO STDOUT", "Copy of the signed contract"),
    ("DESCRIBE onboarding", "Describe the onboarding process"),
    ("USE force.policy", "Use of force policy"),
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


def test_both_real_parsers_consume_and_report_multiple_statements() -> None:
    statements = "SELECT 1; SELECT 2"

    assert duckdb_parse_only(statements) is ParseResult.ACCEPTED_MULTIPLE
    assert (
        postgresql_parse_only(statements, _postgres_config())
        is ParseResult.ACCEPTED_MULTIPLE
    )
    assert contains_public_sql(statements)

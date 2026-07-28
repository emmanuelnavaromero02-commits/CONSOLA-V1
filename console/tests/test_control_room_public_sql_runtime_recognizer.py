from __future__ import annotations

from pathlib import Path

import pytest

from app.services.public_sql_runtime_catalog import (
    DUCKDB_GRAMMAR_VERSION,
    POSTGRESQL_GRAMMAR_VERSION,
    SQL_RUNTIME_CATALOG_VERSION,
)
from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql


AMBIGUOUS_SQL = (
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
)
KEYWORD_INITIAL_BUSINESS_COPY = (
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
)
SQL_PREFIX_IN_BUSINESS_COPY = (
    "Show me the Q4 report",
    "Describe the onboarding process",
    "Use of force policy",
    "Set goals to improve performance",
    "Set expectations to align teams",
)
DIALECT_CONTROLS = (
    "SHOW",
    "SHOW ALL TABLES",
    "DESC payroll",
    "FROM payroll employees",
    "USE memory.main",
)
STRUCTURAL_STATEMENTS = (
    "CALL refresh_payroll()",
    "SET search_path TO private",
    "SET LOCAL search_path TO private",
    "SET NAMES UTF8",
    "COPY payroll TO STDOUT",
    "GRANT SELECT ON payroll TO analyst",
    "MERGE INTO payroll USING staging ON payroll.id = staging.id",
    "FROM memory.main.payroll AS p JOIN benefits AS b USING (employee_id)",
    "FROM read_parquet('private.parquet') AS p WHERE active ORDER BY name LIMIT 1",
    "TABLE memory.main.payroll",
    "VALUES (1), (2)",
    "WITH p AS (TABLE payroll) SELECT * FROM p",
)
EMBEDDED_OR_MULTIPLE_STATEMENTS = (
    "Quarterly review TABLE payroll",
    "Quarterly review; SHOW ALL TABLES",
    "Business copy /* USE memory.main */",
    "Business copy -- DESC payroll\nfor review",
    "Show me the Q4 report; SELECT Comercial",
)
RUNTIME_FULL_STREAM_STATEMENTS = (
    "Quarterly review UPDATE payroll SET salary = 1",
    "Quarterly review MERGE INTO payroll USING staging ON payroll.id = staging.id",
    "Quarterly review TABLE payroll",
    "Note: SHOW ALL TABLES",
    "Note: DESC payroll",
    "Note: USE memory.main",
    "Quarterly review CREATE TABLE payroll(id int)",
    "Quarterly review DELETE FROM payroll",
    "Quarterly review VALUES (1)",
    "Quarterly review; WITH p AS (VALUES (1)) SELECT * FROM p",
)
MALFORMED_SQL_SHAPES = (
    "CALL refresh_payroll(",
    "SET search_path =",
    "COPY payroll TO",
    "GRANT SELECT ON payroll TO",
    "FROM read_parquet(",
    "WITH payroll AS (",
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUOTED_SQL_COPY = (
    "'TABLE Rock'",
    '"SELECT Comercial"',
    "$$TRUNCATE Labs$$",
    "$copy$Show Solutions$copy$",
    "E'Describe Digital'",
)


def test_runtime_catalog_is_pinned_to_test_oracle_grammars() -> None:
    assert DUCKDB_GRAMMAR_VERSION == "1.2.2"
    assert POSTGRESQL_GRAMMAR_VERSION == "15.18"
    assert SQL_RUNTIME_CATALOG_VERSION == "duckdb-1.2.2_postgresql-15.18_v3"


def test_parse_only_oracles_are_pinned_and_wired_into_the_focal_gate() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/control-room-postgres-rls.yml"
    ).read_text(encoding="utf-8")
    helper = (Path(__file__).parent / "public_sql_parse_oracles.py").read_text(
        encoding="utf-8"
    )

    assert "image: postgres:15.18" in workflow
    assert "control_room_public_sql_parse_oracle_gate.py" in workflow
    for variable in (
        "PUBLIC_SQL_POSTGRES_DB",
        "PUBLIC_SQL_POSTGRES_HOST",
        "PUBLIC_SQL_POSTGRES_PORT",
        "PUBLIC_SQL_POSTGRES_USER",
    ):
        assert variable in workflow
    assert "connection.extract_statements(value)" in helper
    assert "connection.execute(" not in helper
    assert '_message(b"P", parse_payload)' in helper
    assert '_message(b"S")' in helper
    assert '_message(b"X")' in helper
    assert '_message(b"B"' not in helper
    assert '_message(b"E"' not in helper


@pytest.mark.parametrize("statement", AMBIGUOUS_SQL)
def test_ambiguous_valid_sql_is_blocked_as_untrusted_raw(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("copy", KEYWORD_INITIAL_BUSINESS_COPY)
def test_keyword_initial_non_statement_is_preserved_without_phrase_allowlist(
    copy: str,
) -> None:
    assert not contains_public_sql(copy)


@pytest.mark.parametrize("copy", SQL_PREFIX_IN_BUSINESS_COPY)
def test_complete_statement_prefix_in_business_copy_is_blocked(copy: str) -> None:
    assert contains_public_sql(copy)


@pytest.mark.parametrize("statement", DIALECT_CONTROLS)
def test_dialect_specific_statement_is_blocked(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", STRUCTURAL_STATEMENTS)
def test_structural_statement_productions_are_blocked(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", EMBEDDED_OR_MULTIPLE_STATEMENTS)
def test_full_stream_scanning_blocks_embedded_statements(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", RUNTIME_FULL_STREAM_STATEMENTS)
def test_runtime_scanner_handles_every_token_boundary(statement: str) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", MALFORMED_SQL_SHAPES)
def test_truncated_structural_statement_fails_closed(statement: str) -> None:
    assert contains_public_sql(statement)


@pytest.mark.parametrize("quoted_copy", QUOTED_SQL_COPY)
def test_statement_text_inside_a_literal_is_not_a_top_level_statement(
    quoted_copy: str,
) -> None:
    assert not contains_public_sql(quoted_copy)


@pytest.mark.parametrize(
    ("statement", "business_copy"),
    (
        ("CALL center()", "Call center roster"),
        ("SET core = values", "Set of core values"),
        ("GRANT portfolio TO reviewer", "Grant Portfolio Review"),
        ("COPY contracts TO STDOUT", "Copy of the signed contract"),
    ),
)
def test_same_head_is_decided_by_production_not_first_word(
    statement: str,
    business_copy: str,
) -> None:
    assert contains_public_sql(statement)
    assert not contains_public_sql(business_copy)

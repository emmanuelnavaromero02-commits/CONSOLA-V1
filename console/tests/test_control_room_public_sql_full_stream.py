from __future__ import annotations

import base64
from urllib.parse import quote

import pytest

from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)


SHOW_ISOLATION_STREAMS = (
    "SHOW TRANSACTION ISOLATION LEVEL",
    "show transaction isolation level",
    "SHOW\tTRANSACTION\nISOLATION\tLEVEL",
    "SHOW TRANSACTION ISOLATION LEVEL business note",
    "Business note. SHOW TRANSACTION ISOLATION LEVEL please",
    "Business note. SHOW TRANSACTION ISOLATION LEVEL",
    "Business note; SHOW TRANSACTION ISOLATION LEVEL; after",
)

EMBEDDED_COMPLETE_STATEMENTS = (
    "Business note. FROM range(10)",
    "Business note. FROM range(10) please",
    "Business note. FROM employees e JOIN payroll p USING (name)",
    "Business note. FROM memory.main.payroll AS p please",
    "Business note. FROM read_parquet('payroll.parquet') AS p "
    "WHERE active ORDER BY name LIMIT 1 please",
    "Business note. FROM payroll USING SAMPLE 10 PERCENT please",
    "Business note. FROM (TABLE payroll) p please",
    "Business note. TABLE payroll LIMIT 1",
    "note TABLE employees please",
    'note TABLE "memory"."payroll" LIMIT 1 please',
    "note TABLE payroll UNION ALL TABLE benefits please",
    "note SHOW ALL TABLES please",
    "SHOW ALL TABLES x",
    "note DESC payroll please",
    "note DESCRIBE payroll please",
    "note SUMMARIZE payroll_internal please",
    'note SUMMARIZE "payroll internal" please',
    "x PIVOT t ON x",
    "note PIVOT (FROM payroll) ON department USING sum(salary) please",
    "x UNPIVOT t ON x INTO NAME metric VALUE amount",
    "note UNPIVOT payroll ON salary, bonus INTO NAME metric VALUE amount please",
    "Business note. DETACH payroll",
    "Business note. LOAD httpfs",
    "Business note. CHECKPOINT payroll",
    "Business note. FORCE CHECKPOINT payroll",
    "Business note. RESET VARIABLE my_var",
    "Business note. PIVOT payroll USING sum(salary) please",
    "Business note. PIVOT payroll GROUP BY department please",
    "Business note. PIVOT_WIDER payroll USING sum(salary) please",
    "Business note. LOCK payroll please",
    "Business note. SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY please",
    "Business note. SET XML OPTION DOCUMENT please",
    "Business note. DO LANGUAGE plpgsql $$BEGIN NULL; END$$ please",
    "Business note. SECURITY LABEL FOR selinux ON ROLE analyst IS 'label' please",
    "Business note. SET LOCAL ROLE analyst please",
    "Business note. SET SESSION ROLE analyst please",
    "Business note. SET SESSION TIME ZONE 'UTC' please",
    "Business note. SET LOCAL XML OPTION DOCUMENT please",
    "Business note. COPY BINARY payroll TO STDOUT please",
    "Business note. USE memory.main please",
    "Business note. TABLE payroll LIMIT 1; after",
    "Business note. FROM /* source */ range(10) please",
    "Business note. -- source\nTABLE payroll LIMIT 1 please",
    "Business note. /* context */ SHOW ALL TABLES please",
)

ALREADY_BLOCKED_PRODUCTION_CONTROLS = (
    "FROM range(10)",
    "TABLE payroll",
    "SHOW ALL TABLES",
    "DESC payroll",
    "SUMMARIZE payroll",
    "PIVOT t ON x",
    "UNPIVOT t ON x INTO NAME metric VALUE amount",
    "Business note. CALL refresh_payroll() please",
    "Business note. COPY payroll TO STDOUT please",
    "Business note. GRANT SELECT ON payroll TO analyst please",
    "Business note. SET search_path TO private please",
    "Business note. UPDATE payroll SET salary = 1",
    "Business note. MERGE INTO payroll USING staging ON payroll.id = staging.id",
    "Business note. INSERT INTO payroll VALUES (1)",
    "Business note. DELETE FROM payroll",
    "Business note. CREATE TABLE payroll(id int)",
    "Business note. VALUES (1)",
    "Business note. WITH p AS (VALUES (1)) SELECT * FROM p",
)

INCOMPLETE_OR_NATURAL_COPY = (
    "employees TABLE",
    "round table",
    "water table",
    "conference table",
    "periodic table",
    "the negotiating table",
    "payroll DESC",
    "monthly SUMMARIZE",
    "candidate PIVOT",
    "candidate UNPIVOT",
    "notes FROM",
    "catalog USE",
)

RAW_SHOW = "Business note. SHOW TRANSACTION ISOLATION LEVEL"
FULLWIDTH_SHOW = "".join(
    chr(ord(character) + 0xFEE0) if "!" <= character <= "~" else character
    for character in RAW_SHOW
)
HOMOGLYPH_SHOW = "Business note. ЅНОԜ TRANSACTION ISOLATION LEVEL"
BASE64_SHOW = base64.b64encode(RAW_SHOW.encode("utf-8")).decode("ascii")
ENCODED_SHOW_FORMS = (
    pytest.param(FULLWIDTH_SHOW, id="nfkc-fullwidth"),
    pytest.param(HOMOGLYPH_SHOW, id="supported-homoglyphs"),
    pytest.param(quote(RAW_SHOW, safe=""), id="percent"),
    pytest.param(r"Business note. \u0053HOW TRANSACTION ISOLATION LEVEL", id="escape"),
    pytest.param(BASE64_SHOW, id="base64"),
    pytest.param(quote(BASE64_SHOW, safe=""), id="percent-then-base64"),
)


def _percent_layers(value: str, layers: int) -> str:
    for _ in range(layers):
        value = quote(value, safe="")
    return value


@pytest.mark.parametrize("statement", SHOW_ISOLATION_STREAMS)
def test_show_transaction_isolation_level_is_blocked_across_stream_positions(
    statement: str,
) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("statement", EMBEDDED_COMPLETE_STATEMENTS)
def test_complete_statement_at_any_top_level_token_position_is_blocked(
    statement: str,
) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("statement", ALREADY_BLOCKED_PRODUCTION_CONTROLS)
def test_existing_structural_and_fail_closed_productions_stay_blocked(
    statement: str,
) -> None:
    assert contains_runtime_sql(statement)
    assert contains_public_sql(statement)
    assert contains_public_technical_copy(statement)
    assert public_business_label(statement) is None


@pytest.mark.parametrize("copy", INCOMPLETE_OR_NATURAL_COPY)
def test_keyword_without_a_complete_production_is_preserved(copy: str) -> None:
    assert not contains_runtime_sql(copy)
    assert not contains_public_sql(copy)
    assert not contains_public_technical_copy(copy)
    assert public_business_label(copy) == copy


@pytest.mark.parametrize("encoded", ENCODED_SHOW_FORMS)
def test_supported_security_equivalents_converge_on_public_blocking(
    encoded: str,
) -> None:
    assert contains_public_technical_copy(encoded)
    assert public_business_label(encoded) is None


@pytest.mark.parametrize("layers", (9, 10))
def test_decode_layer_overflow_remains_fail_closed(layers: int) -> None:
    encoded = _percent_layers("Quarterly workforce review", layers)

    assert contains_public_technical_copy(encoded)
    assert public_business_label(encoded) is None

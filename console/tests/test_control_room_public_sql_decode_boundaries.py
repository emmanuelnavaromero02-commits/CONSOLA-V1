from __future__ import annotations

import base64
import unicodedata
from urllib.parse import quote

import pytest

from app.services import public_path_sensitivity, public_text_sensitivity
from app.services.control_room.diagnostics_public_factory import _public_text
from app.services.control_room.successfactors_gold_observations import (
    _sf_gold_public_widget,
)
from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)


SQL_SELECT_WITH_TRAILING_CLAUSES = (
    "SELECT revenue FROM payroll WHERE active = true",
    "SELECT revenue FROM payroll AS p JOIN benefits AS b ON p.active = b.active",
    "SELECT revenue FROM payroll ORDER BY revenue",
    "SELECT revenue FROM payroll LIMIT 10",
    "SELECT e.name FROM employees e LEFT OUTER JOIN payroll p ON p.name = e.name",
    'SELECT name FROM payroll AS "pay roll" WHERE active = true',
    'SELECT name FROM "pay roll" WHERE active = true',
    "SELECT e.name FROM employees e, payroll p WHERE p.name = e.name",
    f"SELECT name{' ' * 501}FROM payroll WHERE active = true",
    "SELECT name FROM payroll WHERE active",
    "SELECT name FROM payroll WHERE score > 0",
    "SELECT name FROM payroll ORDER BY 1",
    "SELECT e.name FROM employees e "
    "JOIN (SELECT name FROM payroll) p ON p.name = e.name",
    f"SELECT name FROM payroll WHERE {' ' * 513}score > 0",
    "SELECT department, count FROM payroll GROUP BY 1",
    "SELECT name FROM payroll WHERE is_active(name)",
    "SELECT department FROM payroll GROUP BY department HAVING active",
    "SELECT name FROM payroll ORDER BY payroll.name",
    "SELECT name FROM payroll ORDER BY lower(name)",
    "SELECT name FROM unnest(items)",
    "SELECT name FROM (SELECT name FROM payroll) p",
    "SELECT name FROM payroll FOR UPDATE",
    "SELECT name FROM payroll TABLESAMPLE BERNOULLI (10)",
    "SELECT name FROM payroll WHERE name ILIKE pattern",
    "SELECT name FROM payroll WHERE active ORDER BY name",
    "SELECT name FROM payroll WHERE payroll.active",
    "SELECT name FROM payroll WHERE (active)",
    "SELECT x FROM unnest(items) AS t(x)",
    "SELECT x FROM LATERAL unnest(items) AS t(x)",
    "SELECT 'Mr. Smith' AS label FROM payroll WHERE active = true",
    'SELECT name FROM "Payroll. Current" WHERE active = true',
    f"SELECT name FROM ({' ' * 4097}SELECT name FROM payroll) p",
    "Select name from payroll where active = true.",
    "select name from payroll where active::boolean",
    "select name from payroll where active is unknown",
    "select x from unnest(transform(items)) as t(x)",
    "select name from payroll window w as (order by name)",
    "Select name from payroll.",
    "Select name from the payroll where active = true.",
    "select name from payroll using sample 10 percent",
    "select x from unnest(items) with ordinality",
    r"select E'Mr\\\'. Smith' AS label from payroll where active = true",
    "select ? as value from payroll where active = true",
    "select 1. from payroll where active = true",
    "select data ? 'key' from payroll where active = true",
    r"select 'Mr\\\'. Smith' as label from payroll where active = true",
    r"select N'Mr\\\'. Smith' as label from payroll where active = true",
    r"select _utf8'Mr\\\'. Smith' as label from payroll where active = true",
    "Select department from menu.",
    "SELECT candidates FROM talent_pool",
    "SELECT candidates FROM talent pool",
    "Please SELECT candidates FROM talent_pool",
    "SELECT candidates FROM the talent WHERE active = true",
    'SELECT candidates FROM "the talent pool"',
    "SELECT candidates FROM talent.pool",
    "SELECT candidates FROM talent AS pool",
    "SELECT id value FROM ONLY users u",
    "SELECT name ISNULL flag FROM users u",
    "SELECT email NOTNULL present FROM users u",
    "SELECT x ILIKE pattern FROM payroll p",
)
SQL_SELECT_WITHOUT_FROM = (
    r"SELECT E'line\\nvalue'",
    r"SELECT E'from payroll' AS label",
    r"SELECT E'it\'s, FROM payroll' AS label",
    "SELECT $$from payroll$$",
    "SELECT $report$from payroll$report$ AS label",
    "SELECT $report$a, AS, FROM payroll$report$ AS label",
    "SELECT 'People' AS label",
    "SELECT 'People''s team' AS label",
    "SELECT 42 AS value",
    "SELECT NULL AS value",
    "SELECT 1 + 2 AS total",
    "SELECT first_name, last_name",
    "SELECT candidates AS shortlist",
    "SELECT candidates, shortlist",
    'SELECT 1 AS "business value"',
    "SELECT 'People' AS label, 42 AS total",
    "SELECT E'People' AS label, $area$Operations$area$ AS area",
    "SELECT (1 + 2) * 3 AS total, TRUE AS active",
    "SELECT name LIKE pattern",
    "SELECT name NOT LIKE pattern",
    "SELECT name ILIKE pattern",
    "SELECT name SIMILAR TO pattern",
    "SELECT name COLLATE locale",
    "SELECT active AND pending",
    "SELECT active OR pending",
    "SELECT active IS TRUE",
    "SELECT active IS NOT FALSE",
    "SELECT observed AT TIME ZONE utc",
    "SELECT LOCALTIME ORDER BY LOCALTIME",
    "SELECT LOCALTIME GROUP BY LOCALTIME",
    "SELECT LOCALTIME LIMIT ALL",
    "SELECT LOCALTIME FETCH FIRST ROW ONLY",
    "SELECT LOCALTIME INTO snapshot",
    "SELECT candidate WHERE active",
    "SELECT candidate HAVING active",
    "SELECT candidate OFFSET amount",
    "SELECT candidate QUALIFY active",
    "SELECT name GLOB pattern",
    "SELECT /* outer /* inner */ -- */ E'x' AS label",
    "SELECT /* outer /* inner */ -- */ $$x$$ AS label",
    "SELECT /* outer /* inner */ -- */ 42 AS value",
    "SELECT /* outer /* inner */ -- */ LOCALTIME ORDER BY LOCALTIME",
)
BUSINESS_SELECT_INSTRUCTIONS = (
    "Select candidates from the talent pool",
    "select candidates from the talent pool",
    "SELECT CANDIDATES FROM THE TALENT POOL",
    "Select candidates from a talent pool",
    "Select candidates from an internal talent pool",
    "Please select candidates from the talent pool",
    "Please, select candidates from the talent pool.",
    "PLEASE SELECT THE CANDIDATES FROM AN INTERNAL TALENT POOL.",
)
SQL_STATEMENT_BOUNDARY_CANARIES = ("Select name from payroll where active = true;.",)
FULL_STREAM_SQL_CANARIES = (
    "Business note. FROM range(10)",
    "Business note. FROM employees e JOIN payroll p USING (name)",
    "Business note. TABLE payroll LIMIT 1",
    "note TABLE employees please",
    "note SHOW ALL TABLES please",
    "note SUMMARIZE payroll_internal please",
    "x PIVOT t ON x",
    "Business note. DETACH payroll",
    "Business note. LOAD httpfs",
    "Business note. CHECKPOINT payroll",
    "Business note. FORCE CHECKPOINT payroll",
    "Business note. RESET VARIABLE my_var",
)
RAW_SHOW = "Business note. SHOW TRANSACTION ISOLATION LEVEL"
FULLWIDTH_SHOW = "".join(
    chr(ord(character) + 0xFEE0) if "!" <= character <= "~" else character
    for character in RAW_SHOW
)
BASE64_SHOW = base64.b64encode(RAW_SHOW.encode()).decode()
SHOW_SECURITY_EQUIVALENTS = (
    RAW_SHOW,
    FULLWIDTH_SHOW,
    "Business note. ЅНОԜ TRANSACTION ISOLATION LEVEL",
    quote(RAW_SHOW, safe=""),
    r"Business note. \u0053HOW TRANSACTION ISOLATION LEVEL",
    BASE64_SHOW,
    quote(BASE64_SHOW, safe=""),
)


def _encoded_internal_path(layers: int) -> str:
    value = "/srv/private/catalog"
    for _ in range(layers):
        value = quote(value, safe="")
    return value


ENCODED_INTERNAL_PATHS = tuple(
    pytest.param(_encoded_internal_path(layers), id=f"percent-layers-{layers}")
    for layers in (8, 9, 10)
)
NFKC_SQL_EXPANSION = "select " + "\ufdfa" * 460 + " from payroll"
PUBLIC_TECHNICAL_CANARIES = (
    *(
        pytest.param(value, id=f"sql-{index}")
        for index, value in enumerate(
            (
                *SQL_SELECT_WITH_TRAILING_CLAUSES,
                *SQL_SELECT_WITHOUT_FROM,
                *SQL_STATEMENT_BOUNDARY_CANARIES,
                *FULL_STREAM_SQL_CANARIES,
                *SHOW_SECURITY_EQUIVALENTS,
            ),
            start=1,
        )
    ),
    *ENCODED_INTERNAL_PATHS,
    pytest.param(NFKC_SQL_EXPANSION, id="nfkc-sql-expansion"),
)


@pytest.mark.parametrize("statement", SQL_SELECT_WITH_TRAILING_CLAUSES)
def test_select_with_trailing_clause_is_sql_without_semicolon(statement: str) -> None:
    assert ";" not in statement
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", SQL_SELECT_WITHOUT_FROM)
def test_select_projection_is_sql_without_from_or_semicolon(statement: str) -> None:
    assert ";" not in statement
    assert contains_public_sql(statement)


@pytest.mark.parametrize("statement", SQL_STATEMENT_BOUNDARY_CANARIES)
def test_sql_before_prose_punctuation_is_still_blocked(statement: str) -> None:
    assert contains_public_sql(statement)


def test_nfkc_expansion_cannot_move_sql_beyond_the_scan_boundary() -> None:
    normalized = unicodedata.normalize("NFKC", NFKC_SQL_EXPANSION)

    assert len(NFKC_SQL_EXPANSION) < 500
    assert len(normalized) > 8192
    assert contains_public_sql(NFKC_SQL_EXPANSION)
    assert contains_public_sql(normalized)
    assert contains_public_technical_copy(NFKC_SQL_EXPANSION)


def test_shared_policy_computes_the_decode_closure_once(monkeypatch) -> None:
    value = "Operaciones%2520regionales"
    original_scan = public_path_sensitivity.public_encoding_scan
    calls = 0

    def counted_scan(candidate: str):
        nonlocal calls
        calls += 1
        return original_scan(candidate)

    monkeypatch.setattr(public_path_sensitivity, "public_encoding_scan", counted_scan)
    monkeypatch.setattr(public_text_sensitivity, "public_encoding_scan", counted_scan)

    assert not contains_public_technical_copy(value)
    assert calls == 1


@pytest.mark.parametrize("technical", PUBLIC_TECHNICAL_CANARIES)
def test_shared_public_copy_policy_fails_closed(technical: str) -> None:
    assert contains_public_technical_copy(technical)
    assert public_business_label(technical) is None


@pytest.mark.parametrize("technical", PUBLIC_TECHNICAL_CANARIES)
def test_diagnostics_direct_projection_omits_sql_and_decode_overflow(
    technical: str,
) -> None:
    assert _public_text({"message": technical}, "message") == ""


@pytest.mark.parametrize("technical", PUBLIC_TECHNICAL_CANARIES)
def test_gold_direct_projection_invalidates_sql_and_decode_overflow(
    technical: str,
) -> None:
    widget = _sf_gold_public_widget(
        {
            "id": "sf_headcount_by_company",
            "title": "Headcount por compañía",
            "value": 1,
            "status": "ready",
            "rows": [{"company_name": technical, "headcount": 1}],
        }
    )

    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize(
    "raw_copy",
    (*BUSINESS_SELECT_INSTRUCTIONS, "Create policy for annual leave"),
)
def test_untrusted_statement_head_copy_fails_closed(raw_copy: str) -> None:
    assert contains_public_sql(raw_copy)
    assert contains_public_technical_copy(raw_copy)
    assert _public_text({"message": raw_copy}, "message") == ""
    assert public_business_label(raw_copy) is None


def test_shared_policy_preserves_non_statement_business_label() -> None:
    assert not contains_public_technical_copy("Comercio")
    assert _public_text({"message": "Comercio"}, "message") == "Comercio"
    assert public_business_label("Comercio") == "Comercio"

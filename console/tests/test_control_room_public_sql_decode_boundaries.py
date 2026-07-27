from __future__ import annotations

import unicodedata
from urllib.parse import quote

import pytest

from app.services import public_path_sensitivity, public_text_sensitivity
from app.services.control_room.operational_diagnostics import _public_text
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
)
SQL_STATEMENT_BOUNDARY_CANARIES = ("Select name from payroll where active = true;.",)


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
            (*SQL_SELECT_WITH_TRAILING_CLAUSES, *SQL_STATEMENT_BOUNDARY_CANARIES),
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


@pytest.mark.parametrize("statement", SQL_STATEMENT_BOUNDARY_CANARIES)
def test_sql_before_prose_punctuation_is_still_blocked(statement: str) -> None:
    assert contains_public_sql(statement)


def test_nfkc_expansion_cannot_move_sql_beyond_the_scan_boundary() -> None:
    normalized = unicodedata.normalize("NFKC", NFKC_SQL_EXPANSION)

    assert len(NFKC_SQL_EXPANSION) < 500
    assert len(normalized) > 8192
    assert contains_public_sql(NFKC_SQL_EXPANSION)
    assert not contains_public_sql(normalized)
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
    "business_copy",
    (
        "Select department from menu.",
        "Create policy for annual leave",
        "Comercio",
    ),
)
def test_shared_policy_preserves_valid_business_copy(business_copy: str) -> None:
    assert not contains_public_technical_copy(business_copy)
    assert _public_text({"message": business_copy}, "message") == business_copy
    assert public_business_label(business_copy) == business_copy

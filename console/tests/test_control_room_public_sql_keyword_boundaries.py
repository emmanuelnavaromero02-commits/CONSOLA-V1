from __future__ import annotations

import pytest

from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)


EMBEDDED_COMPLETE_PRODUCTIONS = (
    "Business note. FROM range(10)",
    "Business note. FROM employees e JOIN payroll p USING (name)",
    "Business note. TABLE payroll LIMIT 1",
    "note TABLE employees please",
    "note SHOW ALL TABLES please",
    "SHOW ALL TABLES x",
    "note SUMMARIZE payroll_internal please",
    "x PIVOT t ON x",
    "Business note. DETACH payroll",
    "Business note. LOAD httpfs",
    "Business note. CHECKPOINT payroll",
    "Business note. FORCE CHECKPOINT payroll",
    "Business note. RESET VARIABLE my_var",
)

RAW_ID_ONLY_COPY = (
    "Show me the Q4 report",
    "Describe the onboarding process",
    "Use of force policy",
    "Set goals to improve performance",
    "Set expectations to align teams",
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
    "Set goals to win",
    "Set priorities to high",
)

RAW_SAFE_COPY = (
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
    "Sales Receipts",
    "Gross Receipts",
    "State: California",
    "State: Texas",
    "Status: Won",
    "Data Status: Green",
    "Binding Agreements",
    "Supply Chain Provenance",
    "State pension review",
    "Status meeting today",
    "Receipt of annual leave request",
    "Binding employment agreement",
    "Provenance of organic coffee",
    "Call center roster;",
    "Set of core values;",
    "Copy of the signed contract;",
    "Copy of the signed contract to archive",
    "Grant Portfolio Review to leadership",
    "Pivot the hiring strategy on Monday",
)

TABLE_NON_PRODUCTIONS = (
    "TABLE",
    "employees TABLE",
    "round table",
    "water table",
    "conference table",
    "periodic table",
    "the negotiating table",
)

HEAD_DECISION_PAIRS = (
    (
        "show-id-only",
        "note SHOW ALL TABLES please",
        "Show me the Q4 report",
        True,
    ),
    (
        "describe-id-only",
        "note DESCRIBE payroll please",
        "Describe the onboarding process",
        True,
    ),
    (
        "use-id-only",
        "note USE memory.main please",
        "Use of force policy",
        True,
    ),
    (
        "set-id-only",
        "note SET goals TO improve please",
        "Set goals to improve performance",
        True,
    ),
    (
        "call-safe",
        "note CALL refresh_payroll() please",
        "Call center roster",
        False,
    ),
    (
        "set-safe",
        "note SET search_path TO private please",
        "Set of core values",
        False,
    ),
    (
        "grant-safe",
        "note GRANT SELECT ON payroll TO analyst please",
        "Grant Portfolio Review",
        False,
    ),
    (
        "copy-safe",
        "note COPY payroll TO STDOUT please",
        "Copy of the signed contract",
        False,
    ),
    (
        "table-trailing-safe",
        "note TABLE employees please",
        "employees TABLE",
        False,
    ),
    (
        "from-trailing-safe",
        "Business note. FROM range(10)",
        "Applicants travelling FROM",
        False,
    ),
    (
        "summarize-trailing-safe",
        "note SUMMARIZE payroll_internal please",
        "We plan to SUMMARIZE",
        False,
    ),
    (
        "pivot-grammar-safe",
        "x PIVOT t ON x",
        "Pivot the hiring strategy",
        False,
    ),
)


def _direct_policy(value: str) -> tuple[bool, bool, bool, str | None]:
    return (
        contains_runtime_sql(value),
        contains_public_sql(value),
        contains_public_technical_copy(value),
        public_business_label(value),
    )


def _blocked() -> tuple[bool, bool, bool, None]:
    return True, True, True, None


def _preserved(value: str) -> tuple[bool, bool, bool, str]:
    return False, False, False, value


@pytest.mark.parametrize("value", EMBEDDED_COMPLETE_PRODUCTIONS)
def test_complete_production_is_blocked_at_any_stream_position(value: str) -> None:
    assert _direct_policy(value) == _blocked()


@pytest.mark.parametrize("value", RAW_ID_ONLY_COPY)
def test_sql_ambiguous_raw_copy_requires_server_owned_id(value: str) -> None:
    assert _direct_policy(value) == _blocked()


@pytest.mark.parametrize("value", RAW_SAFE_COPY)
def test_raw_copy_without_complete_production_is_preserved(value: str) -> None:
    assert _direct_policy(value) == _preserved(value)


@pytest.mark.parametrize("value", TABLE_NON_PRODUCTIONS)
def test_table_without_relation_production_is_preserved(value: str) -> None:
    assert _direct_policy(value) == _preserved(value)


@pytest.mark.parametrize(
    ("_head", "production"),
    tuple((case[0], case[1]) for case in HEAD_DECISION_PAIRS),
    ids=[case[0] for case in HEAD_DECISION_PAIRS],
)
def test_same_head_is_decided_by_complete_production(
    _head: str,
    production: str,
) -> None:
    assert _direct_policy(production) == _blocked()


@pytest.mark.parametrize(
    ("_head", "_production", "copy", "copy_is_id_only"),
    HEAD_DECISION_PAIRS,
    ids=[case[0] for case in HEAD_DECISION_PAIRS],
)
def test_same_head_copy_obeys_its_own_complete_production_boundary(
    _head: str,
    _production: str,
    copy: str,
    copy_is_id_only: bool,
) -> None:
    expected_copy = _blocked() if copy_is_id_only else _preserved(copy)
    assert _direct_policy(copy) == expected_copy

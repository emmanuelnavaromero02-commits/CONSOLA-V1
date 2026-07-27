from __future__ import annotations

import pytest

from app.services.control_room.operational_diagnostics import _public_text
from app.services.control_room.successfactors_gold_observations import (
    _sf_gold_public_widget,
)
from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)


HIDDEN_QUERY_SUFFIXES = (
    "Select candidates from the talent pool SELECT salary",
    "Select candidates from the talent pool TABLE employees",
    "Select candidates from the talent pool SUMMARIZE employees",
    "SELECT",
)
ADVERSARIAL_QUERY_FORMS = (
    "Select SELECT salary from the talent pool",
    "Select TABLE employees from the talent pool",
    "Select SUMMARIZE employees from the talent pool",
    "Select candidates from the talent pool SHOW TABLES",
    "Select candidates from the talent pool CHECKPOINT",
    "Select candidates from the talent pool FORCE CHECKPOINT",
    "Select candidates from the talent pool BEGIN",
    "Select candidates from the talent pool COMMIT",
    "Select candidates from the talent pool ROLLBACK WORK",
    "Select candidates from the talent pool VACUUM",
    "Select candidates from the talent pool ANALYZE",
    "Select candidates from the talent pool ANALYSE.",
    "Select candidates from the talent pool CLUSTER",
    "Select candidates from the talent pool RESET ROLE",
    "Select candidates from the talent pool LISTEN channel",
    "Select candidates from the talent pool UNLISTEN channel",
    "Select candidates from the talent pool DETACH workforce",
    "Select candidates from the talent pool LOAD json",
    "Select candidates from the talent pool VALUES employees",
    "Select candidates from the talent pool PIVOT employees",
    "Select candidates from the talent pool UNPIVOT employees",
    "Select candidates from the talent pool CREATE TYPE shelltype",
    "Select candidates from the talent pool DROP TYPE shelltype",
    "Select candidates from the talent pool DROP INDEX idx",
    "Select candidates from the talent pool DISCARD PLANS",
    "Select candidates from the talent pool LOCK TABLE employees",
    "Select candidates from the talent pool SET SESSION AUTHORIZATION analyst",
    "Select candidates from the talent pool RELEASE checkpoint_name",
    "Select candidates from the shortlist DESCRIBE employees",
    "Select candidates from the talent pool SHOW.",
    "Select candidates from the talent pool START.",
    "Select candidates from the talent pool DESCRIBE.",
    "Select candidates from the shortlist USE workforce",
    "Select candidates from the shortlist TRUNCATE employees",
    "Select candidates from the shortlist CLOSE cursor_name",
    "Select candidates from the shortlist DEALLOCATE plan_name",
    "Select candidates from the shortlist EXECUTE plan_name",
    "Select candidates from the shortlist INSTALL json",
    "Select candidates from the shortlist NOTIFY channel",
    "Select candidates from the shortlist SAVEPOINT checkpoint_name",
    "Select candidates from the LOCK employees.",
    "Select candidates from the shortlist MOVE cursor_name",
    "Select candidates from the talent pool for LOCK employees.",
    "Select REFRESH MATERIALIZED VIEW workforce from the talent pool",
    "Select EXEC proc from the talent pool",
    "Select salary - bonus total from payroll employees.",
    "Select candidates from the talent pool-SELECT salary",
    "Select candidates from the talent pool-TABLE employees",
    "Select candidates from the talent pool-SUMMARIZE employees",
    "Select candidates from the talent pool'SELECT salary",
    "Select candidates from the talent pool’SELECT salary",
    "Select candidates from the talent pool–SELECT salary",
    "SELECT -- note",
)
BUSINESS_SELECT_INSTRUCTIONS = (
    "Please select candidates from available employees.",
    "Select candidates from your talent pool.",
    "Select candidates from the shortlist.",
    "Select candidates from available talent pools.",
    "Select all candidates from the talent pool.",
    "Please select candidates from the talent pool for review.",
    "Select candidates from the first talent pool.",
    "Select candidates from the talent pool in Madrid.",
    "Select high-potential candidates from the talent pool.",
    "Select candidates from the company's pool.",
    "Select candidates from the company’s pool.",
)
QUOTED_SELECT_CONTROLS = ("'SELECT'", '"SELECT"', "$$SELECT$$", "E'SELECT'")
AMBIGUOUS_UNWRAPPED_SQL = (
    "Select candidates from the shortlist",
    "SELECT candidates FROM THE SHORTLIST.",
    "Select candidates from your shortlist.",
    "select candidates from your shortlist.",
    "Please SELECT candidates from available employees.",
    "Please select candidates from available employees",
)


def _gold_widget(label: str) -> dict:
    return _sf_gold_public_widget(
        {
            "id": "sf_headcount_by_company",
            "title": "Headcount por compañía",
            "value": 1,
            "status": "ready",
            "rows": [{"company_name": label, "headcount": 1}],
        }
    )


@pytest.mark.parametrize("query", HIDDEN_QUERY_SUFFIXES)
def test_hidden_query_fails_closed_in_shared_direct_policy(query: str) -> None:
    assert contains_public_sql(query)
    assert contains_public_technical_copy(query)
    assert public_business_label(query) is None


@pytest.mark.parametrize("query", HIDDEN_QUERY_SUFFIXES)
def test_hidden_query_is_omitted_by_direct_diagnostics(query: str) -> None:
    assert _public_text({"message": query}, "message") == ""


@pytest.mark.parametrize("query", HIDDEN_QUERY_SUFFIXES)
def test_hidden_query_invalidates_direct_gold(query: str) -> None:
    widget = _gold_widget(query)
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_natural_business_instruction_survives_shared_policy_byte_exact(
    business_copy: str,
) -> None:
    assert not contains_public_sql(business_copy)
    assert not contains_public_technical_copy(business_copy)
    assert public_business_label(business_copy) == business_copy


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_natural_business_instruction_survives_direct_diagnostics_byte_exact(
    business_copy: str,
) -> None:
    assert _public_text({"message": business_copy}, "message") == business_copy


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_natural_business_instruction_survives_direct_gold_byte_exact(
    business_copy: str,
) -> None:
    widget = _gold_widget(business_copy)
    assert widget["status"] == "ready"
    assert widget["value"] == 1
    assert widget["rows"] == [
        {
            "company_name": business_copy,
            "label": business_copy,
            "headcount": 1,
        }
    ]


@pytest.mark.parametrize("query", ADVERSARIAL_QUERY_FORMS)
def test_adversarial_complete_query_forms_cannot_hide_in_instruction(
    query: str,
) -> None:
    assert contains_public_sql(query)
    assert contains_public_technical_copy(query)


@pytest.mark.parametrize("quoted", QUOTED_SELECT_CONTROLS)
def test_quoted_select_word_is_not_a_top_level_statement(quoted: str) -> None:
    assert not contains_public_sql(quoted)


@pytest.mark.parametrize("query", AMBIGUOUS_UNWRAPPED_SQL)
def test_low_confidence_table_alias_sql_requires_exact_natural_framing(
    query: str,
) -> None:
    assert contains_public_sql(query)

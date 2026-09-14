"""Risk aggregates — unit tests over the fake asyncpg plumbing.

attrition_risk_population reuses Talent's retention_risk dataset (no score
recompute) and the official affected_count from talent_action_candidates;
employment_end_expiry is the honest proxy for contract expiry; deal_slippage
reads salesforce_deals_en_riesgo. Covers scope, degraded/unavailable paths,
PII exclusion and bounded top-N.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.intelligence import risk_aggregates as risk
from app.services.intelligence.domain_aggregate_support import MAX_GROUP_ROWS
from tests.domain_aggregate_fakes import (
    TENANT_A,
    WORKSPACE_A,
    FakeConn,
    FakeGoldDataset,
    install_gold_connect,
    user_for,
)

AS_OF = date(2026, 9, 13)

RETENTION_COLUMNS = {
    "user_id": "text",
    "company_name": "text",
    "department_name": "text",
    "location_name": "text",
    "job_code": "text",
    "role_name": "text",
    "readiness_status": "text",
    "fit_score": "numeric",
    "invalid_score_input": "boolean",
    "movement_events": "bigint",
    "months_since_movement": "numeric",
    "retention_risk_score": "numeric",
    "risk_band": "text",
    "status": "text",
    "blockers": "text",
    "generated_at": "timestamp",
}
ACTION_COLUMNS = {
    "action_id": "text",
    "action_type": "text",
    "severity": "text",
    "affected_count": "bigint",
    "title": "text",
    "recommendation": "text",
    "status": "text",
}
EMPLOYEE_COLUMNS = {
    "user_id_hash": "text",
    "full_name": "text",
    "department_name": "text",
    "company_name": "text",
    "cost_center": "text",
    "start_date": "date",
    "end_date": "date",
    "is_active": "boolean",
}
DEALS_COLUMNS = {
    "opportunity_id": "text",
    "opportunity_name": "text",
    "vendedor": "text",
    "stage_name": "text",
    "amount": "numeric",
    "close_date": "date",
    "last_activity": "date",
    "dias_sin_actividad": "bigint",
    "motivo_riesgo": "text",
}


def _without(columns: dict[str, str], *names: str) -> dict[str, str]:
    return {key: value for key, value in columns.items() if key not in names}


def _all_sql(conn: FakeConn) -> str:
    return "\n".join(sql for _method, sql, _args in conn.calls)


# ── R1 attrition risk population ────────────────────────────────────────────


def _attrition_answers() -> dict:
    return {
        "risk.attrition_risk_population.bands": {
            "total": 6160,
            "high": 6000,
            "medium": 100,
            "low": 50,
            "insufficient_data": 10,
            "avg_score_valid": Decimal("71.25"),
        },
        "risk.attrition_risk_population.departments": [
            {
                "department_name": "Operaciones",
                "total": 4000,
                "high": 3900,
                "medium": 50,
            },
            {"department_name": "Ventas", "total": 2100, "high": 2100, "medium": 0},
        ],
        "risk.attrition_risk_population.talent_action": {
            "affected_count": 6000,
            "severity": "high",
            "action_rows": 1,
        },
    }


@pytest.mark.asyncio
async def test_attrition_risk_population_reuses_talent(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.RETENTION_RISK_DATASET: FakeGoldDataset(
                risk.RETENTION_RISK_DATASET, RETENTION_COLUMNS
            ),
            risk.ACTION_CANDIDATES_DATASET: FakeGoldDataset(
                risk.ACTION_CANDIDATES_DATASET, ACTION_COLUMNS
            ),
        },
        answers=_attrition_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_attrition_risk_population(user_for(), top_n=10)

    assert result.status == "ready"
    assert result.supported is True
    assert result.proxy_note is None
    assert result.total == 6160
    assert result.high == 6000
    assert result.medium == 100
    assert result.low == 50
    assert result.insufficient_data == 10
    assert result.avg_score_valid == 71.25
    assert [item["department_name"] for item in result.departments_top] == [
        "Operaciones",
        "Ventas",
    ]
    assert result.talent_action == {
        "action_id": "talent_retention_risk",
        "affected_count": 6000,
        "severity": "high",
        "present": True,
    }
    assert any("no se recalcula" in note for note in result.notes)

    bands_sql = conn.sql_for("risk.attrition_risk_population.bands")
    assert "COUNT(*) FILTER (WHERE risk_band = 'high')" in bands_sql
    assert (
        "AVG(retention_risk_score) FILTER (WHERE risk_band IN ('high', 'medium', 'low'))"
        in bands_sql
    )
    assert "workspace_id::text = $1 AND tenant_id::text = $2" in bands_sql
    assert conn.args_for("risk.attrition_risk_population.bands") == (
        WORKSPACE_A,
        TENANT_A,
    )
    assert "HAVING COUNT(*) FILTER (WHERE risk_band = 'high') > 0" in conn.sql_for(
        "risk.attrition_risk_population.departments"
    )
    assert conn.args_for("risk.attrition_risk_population.departments")[-1] == 10
    assert conn.args_for("risk.attrition_risk_population.talent_action") == (
        WORKSPACE_A,
        TENANT_A,
        "talent_retention_risk",
    )
    # Person-level columns never reach SQL text: aggregates only.
    executed = _all_sql(conn)
    assert "user_id" not in executed.replace("workspace_id", "").replace(
        "tenant_id", ""
    )
    assert "full_name" not in executed
    assert "talent_percent_is_valid" not in executed  # DuckDB macro, not Postgres
    assert [ref["dataset"] for ref in result.evidence_refs] == [
        risk.RETENTION_RISK_DATASET,
        risk.ACTION_CANDIDATES_DATASET,
    ]
    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert conn.scope == (TENANT_A, WORKSPACE_A)


@pytest.mark.asyncio
async def test_attrition_risk_population_without_action_dataset(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.RETENTION_RISK_DATASET: FakeGoldDataset(
                risk.RETENTION_RISK_DATASET, RETENTION_COLUMNS
            )
        },
        answers=_attrition_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_attrition_risk_population(user_for())

    assert result.status == "ready"
    assert result.high == 6000
    assert result.talent_action is None
    assert any(
        "talent_action_candidates no disponible" in note for note in result.notes
    )
    assert "risk.attrition_risk_population.talent_action" not in conn.markers()


@pytest.mark.asyncio
async def test_attrition_risk_population_action_absent_means_zero(monkeypatch):
    answers = _attrition_answers()
    answers["risk.attrition_risk_population.talent_action"] = {
        "affected_count": None,
        "severity": None,
        "action_rows": 0,
    }
    conn = FakeConn(
        datasets={
            risk.RETENTION_RISK_DATASET: FakeGoldDataset(
                risk.RETENTION_RISK_DATASET, RETENTION_COLUMNS
            ),
            risk.ACTION_CANDIDATES_DATASET: FakeGoldDataset(
                risk.ACTION_CANDIDATES_DATASET, ACTION_COLUMNS
            ),
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_attrition_risk_population(user_for())

    assert result.talent_action == {
        "action_id": "talent_retention_risk",
        "affected_count": 0,
        "severity": "low",
        "present": False,
    }
    assert any("Talent reporta 0 empleados" in note for note in result.notes)


@pytest.mark.asyncio
async def test_attrition_risk_population_degrades_without_department_and_score(
    monkeypatch,
):
    answers = _attrition_answers()
    answers["risk.attrition_risk_population.bands"]["avg_score_valid"] = None
    conn = FakeConn(
        datasets={
            risk.RETENTION_RISK_DATASET: FakeGoldDataset(
                risk.RETENTION_RISK_DATASET,
                _without(RETENTION_COLUMNS, "department_name", "retention_risk_score"),
            ),
            risk.ACTION_CANDIDATES_DATASET: FakeGoldDataset(
                risk.ACTION_CANDIDATES_DATASET, ACTION_COLUMNS
            ),
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_attrition_risk_population(user_for())

    assert result.status == "degraded"
    assert result.high == 6000
    assert result.avg_score_valid is None
    assert result.departments_top == []
    assert "risk.attrition_risk_population.departments" not in conn.markers()
    assert result.missing_columns == [
        f"{risk.RETENTION_RISK_DATASET}.department_name",
        f"{risk.RETENTION_RISK_DATASET}.retention_risk_score",
    ]
    assert "NULL::float8 AS avg_score_valid" in conn.sql_for(
        "risk.attrition_risk_population.bands"
    )


@pytest.mark.asyncio
async def test_attrition_risk_population_unavailable_paths(monkeypatch):
    conn = FakeConn(datasets={}, answers=_attrition_answers())
    install_gold_connect(monkeypatch, conn)
    result = await risk.query_attrition_risk_population(user_for())
    assert result.status == "unavailable"
    assert (
        result.error == f"missing: dataset unavailable: {risk.RETENTION_RISK_DATASET}"
    )

    conn = FakeConn(
        datasets={
            risk.RETENTION_RISK_DATASET: FakeGoldDataset(
                risk.RETENTION_RISK_DATASET, _without(RETENTION_COLUMNS, "risk_band")
            )
        },
        answers=_attrition_answers(),
    )
    install_gold_connect(monkeypatch, conn)
    result = await risk.query_attrition_risk_population(user_for())
    assert result.status == "unavailable"
    assert result.error.startswith("invalid_schema:")
    assert result.missing_columns == ["risk_band"]


# ── R3 employment end expiry ────────────────────────────────────────────────


def _expiry_answers() -> dict:
    return {
        "risk.employment_end_expiry.windows": {
            "within_30": 4,
            "within_60": 9,
            "within_90": 15,
        },
        "risk.employment_end_expiry.departments": [
            {
                "department_name": "Soporte",
                "within_30": 3,
                "within_60": 5,
                "within_90": 8,
            },
            {
                "department_name": "Ventas",
                "within_30": 1,
                "within_60": 4,
                "within_90": 7,
            },
        ],
    }


@pytest.mark.asyncio
async def test_employment_end_expiry_ready(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.EMPLOYEE_360_DATASET: FakeGoldDataset(
                risk.EMPLOYEE_360_DATASET, EMPLOYEE_COLUMNS
            )
        },
        answers=_expiry_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_employment_end_expiry(user_for(), as_of=AS_OF)

    assert result.status == "ready"
    assert result.proxy_note == risk.EMPLOYMENT_END_PROXY_NOTE
    assert "NO un elemento contractual" in result.proxy_note
    assert (result.within_30, result.within_60, result.within_90) == (4, 9, 15)
    assert result.active_filter_applied is True
    assert result.sentinel_excluded_from == date(2030, 1, 1)
    assert [item["department_name"] for item in result.departments_top] == [
        "Soporte",
        "Ventas",
    ]

    windows_sql = conn.sql_for("risk.employment_end_expiry.windows")
    assert "end_date::date < $7::date" in windows_sql
    assert "EXTRACT(" not in windows_sql
    assert "AND is_active IS TRUE" in windows_sql
    assert "end_date::date > $3::date" in windows_sql
    assert "end_date::date <= $6::date" in windows_sql
    assert conn.args_for("risk.employment_end_expiry.windows") == (
        WORKSPACE_A,
        TENANT_A,
        AS_OF,
        AS_OF + timedelta(days=30),
        AS_OF + timedelta(days=60),
        AS_OF + timedelta(days=90),
        date(2030, 1, 1),
    )
    assert conn.args_for("risk.employment_end_expiry.departments")[-1] == 10
    assert "LIMIT $8" in conn.sql_for("risk.employment_end_expiry.departments")
    assert "full_name" not in _all_sql(conn)
    filters = result.evidence_refs[0]["filters"]
    assert filters["windows_days"] == [30, 60, 90]
    assert filters["active_only"] is True


@pytest.mark.asyncio
async def test_employment_end_expiry_degrades_without_is_active(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.EMPLOYEE_360_DATASET: FakeGoldDataset(
                risk.EMPLOYEE_360_DATASET,
                _without(EMPLOYEE_COLUMNS, "is_active", "department_name"),
            )
        },
        answers=_expiry_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_employment_end_expiry(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.active_filter_applied is False
    assert result.departments_top == []
    assert "is_active" not in conn.sql_for("risk.employment_end_expiry.windows")
    assert any("is_active ausente" in note for note in result.notes)
    assert any("department_name ausente" in note for note in result.notes)


@pytest.mark.asyncio
async def test_employment_end_expiry_unavailable_without_end_date(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.EMPLOYEE_360_DATASET: FakeGoldDataset(
                risk.EMPLOYEE_360_DATASET, _without(EMPLOYEE_COLUMNS, "end_date")
            )
        },
        answers=_expiry_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_employment_end_expiry(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.missing_columns == ["end_date"]
    assert conn.markers() == []


# ── R4 deal slippage ────────────────────────────────────────────────────────


def _slippage_answers() -> dict:
    return {
        "risk.deal_slippage.totals": {
            "deals": 6010,
            "amount_total": Decimal("1234567.89"),
            "deals_1_30": 6000,
            "amount_1_30": Decimal("1200000"),
            "deals_31_60": 10,
            "amount_31_60": Decimal("34567.89"),
            "deals_over_60": 0,
            "amount_over_60": Decimal("0"),
        },
        "risk.deal_slippage.by_stage": [
            {"stage_name": "Negotiation", "deals": 4000, "amount": Decimal("900000")},
            {"stage_name": "Proposal", "deals": 2010, "amount": Decimal("334567.89")},
        ],
        "risk.deal_slippage.top_deals": [
            {
                "opportunity_name": "Big One",
                "stage_name": "Negotiation",
                "amount": Decimal("250000"),
                "close_date": AS_OF - timedelta(days=12),
                "days_overdue": 12,
                "days_without_activity": 20,
                "risk_reason": "cierre vencido",
            }
        ],
    }


@pytest.mark.asyncio
async def test_deal_slippage_ready(monkeypatch):
    conn = FakeConn(
        datasets={
            risk.DEALS_AT_RISK_DATASET: FakeGoldDataset(
                risk.DEALS_AT_RISK_DATASET, DEALS_COLUMNS
            )
        },
        answers=_slippage_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_deal_slippage(user_for(), as_of=AS_OF)

    assert result.status == "ready"
    assert result.proxy_note is None
    assert result.deals == 6010
    assert result.amount_total == 1234567.89
    assert result.buckets["1_30"] == {"deals": 6000, "amount": 1200000.0}
    assert result.buckets["31_60"] == {"deals": 10, "amount": 34567.89}
    assert result.buckets["over_60"] == {"deals": 0, "amount": 0.0}
    assert [item["stage_name"] for item in result.by_stage] == [
        "Negotiation",
        "Proposal",
    ]
    assert result.top_deals[0]["opportunity_name"] == "Big One"
    assert result.top_deals[0]["days_overdue"] == 12
    assert "vendedor" not in result.top_deals[0]
    assert any("sin conversion de moneda" in note for note in result.notes)

    totals_sql = conn.sql_for("risk.deal_slippage.totals")
    assert "close_date::date < $3::date" in totals_sql
    assert "($3::date - close_date::date) BETWEEN 1 AND 30" in totals_sql
    assert (
        "COALESCE(SUM(amount) FILTER (WHERE ($3::date - close_date::date) > 60), 0)"
        in totals_sql
    )
    assert conn.args_for("risk.deal_slippage.totals") == (WORKSPACE_A, TENANT_A, AS_OF)
    assert conn.args_for("risk.deal_slippage.top_deals") == (
        WORKSPACE_A,
        TENANT_A,
        AS_OF,
        5,
    )
    assert "ORDER BY amount DESC NULLS LAST" in conn.sql_for(
        "risk.deal_slippage.top_deals"
    )
    assert "vendedor" not in _all_sql(conn)  # seller name never selected
    assert "is_closed" not in _all_sql(conn)  # dataset already filters it
    assert result.evidence_refs[0]["dataset"] == risk.DEALS_AT_RISK_DATASET
    assert (
        result.to_dict()["top_deals"][0]["close_date"]
        == (AS_OF - timedelta(days=12)).isoformat()
    )


@pytest.mark.asyncio
async def test_deal_slippage_degrades_without_amount_and_stage(monkeypatch):
    answers = _slippage_answers()
    totals = answers["risk.deal_slippage.totals"]
    for key in ("amount_total", "amount_1_30", "amount_31_60", "amount_over_60"):
        totals[key] = None
    answers["risk.deal_slippage.top_deals"][0]["amount"] = None
    answers["risk.deal_slippage.top_deals"][0]["stage_name"] = None
    conn = FakeConn(
        datasets={
            risk.DEALS_AT_RISK_DATASET: FakeGoldDataset(
                risk.DEALS_AT_RISK_DATASET,
                _without(DEALS_COLUMNS, "amount", "stage_name"),
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await risk.query_deal_slippage(user_for(), top_n=999, as_of=AS_OF)

    assert result.status == "degraded"
    assert result.deals == 6010
    assert result.amount_total is None
    assert result.buckets["1_30"] == {"deals": 6000, "amount": None}
    assert result.by_stage == []
    assert "risk.deal_slippage.by_stage" not in conn.markers()
    assert result.missing_columns == [
        f"{risk.DEALS_AT_RISK_DATASET}.amount",
        f"{risk.DEALS_AT_RISK_DATASET}.stage_name",
    ]
    totals_sql = conn.sql_for("risk.deal_slippage.totals")
    assert "NULL::float8 AS amount_total" in totals_sql
    assert "SUM(amount)" not in totals_sql
    assert "ORDER BY days_overdue DESC" in conn.sql_for("risk.deal_slippage.top_deals")
    assert conn.args_for("risk.deal_slippage.top_deals")[-1] == MAX_GROUP_ROWS


@pytest.mark.asyncio
async def test_deal_slippage_unavailable_paths(monkeypatch):
    conn = FakeConn(datasets={}, answers=_slippage_answers())
    install_gold_connect(monkeypatch, conn)
    result = await risk.query_deal_slippage(user_for(), as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error == f"missing: dataset unavailable: {risk.DEALS_AT_RISK_DATASET}"

    conn = FakeConn(
        datasets={
            risk.DEALS_AT_RISK_DATASET: FakeGoldDataset(
                risk.DEALS_AT_RISK_DATASET, _without(DEALS_COLUMNS, "close_date")
            )
        },
        answers=_slippage_answers(),
    )
    install_gold_connect(monkeypatch, conn)
    result = await risk.query_deal_slippage(user_for(), as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error.startswith("invalid_schema:")
    assert result.missing_columns == ["close_date"]

    connects = install_gold_connect(monkeypatch, FakeConn(answers=_slippage_answers()))
    result = await risk.query_deal_slippage({"workspace_id": WORKSPACE_A}, as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error.startswith("no_permission:")
    assert connects() == []

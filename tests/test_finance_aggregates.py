"""Finance aggregates — unit tests over the fake asyncpg plumbing.

Covers, per the Mission 1 contract: relation missing -> unavailable, optional
column missing -> degraded, required column missing -> unavailable
(invalid_schema), tenant/workspace scope enforced (GUCs + explicit predicate),
correct aggregate wiring with a small fixture, bounded top-N, and JSON output.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from app.services.intelligence import finance_aggregates as fin
from app.services.intelligence.domain_aggregate_support import (
    MAX_GROUP_ROWS,
    MAX_NAMED_ROWS,
)
from tests.domain_aggregate_fakes import (
    TENANT_A,
    WORKSPACE_A,
    FakeConn,
    FakeGoldDataset,
    install_gold_connect,
    user_for,
)

AS_OF = date(2026, 9, 13)
PUBLISHED_AT = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)

CONSULTOR_COLUMNS = {
    "mes": "date",
    "proyecto": "text",
    "project_name": "text",
    "cliente": "text",
    "consultor": "text",
    "horas_facturables": "numeric",
    "billing_rate_usd": "numeric",
    "revenue_generado": "numeric",
}
COSTO_COLUMNS = {
    "mes": "date",
    "userid": "text",
    "nombre_completo": "text",
    "departamento": "text",
    "rate_costo": "numeric",
    "horas_ejecutadas": "numeric",
    "costo_ejecutado": "numeric",
    "costo_hundido": "numeric",
    "costo_potencial_mes": "numeric",
}
PNL_COLUMNS = {
    "mes": "date",
    "proyecto": "text",
    "project_name": "text",
    "cliente": "text",
    "tipo_proyecto": "text",
    "revenue_base_amount": "numeric",
    "cost_direct_base_amount": "numeric",
    "cost_sunk_base_amount": "numeric",
    "original_billing_amount": "numeric",
    "original_currency": "text",
    "horas_facturables": "numeric",
    "financial_status": "text",
}


def _dataset(name: str, columns: dict[str, str], **kwargs) -> FakeGoldDataset:
    return FakeGoldDataset(name, columns, published_at=PUBLISHED_AT, **kwargs)


def _without(columns: dict[str, str], *names: str) -> dict[str, str]:
    return {key: value for key, value in columns.items() if key not in names}


# ── F1 billable hours logged ────────────────────────────────────────────────


def _billable_answers() -> dict:
    return {
        "finance.billable_hours_logged.totals": {
            "billable_hours": Decimal("120.5"),
            "billable_amount_usd": Decimal("9040.00"),
            "projects_affected": 3,
            "contributors": 4,
        },
        "finance.billable_hours_logged.top_projects": [
            {
                "proyecto": "P-1",
                "project_name": "Alpha",
                "cliente": "ACME",
                "billable_hours": Decimal("80"),
                "billable_amount_usd": Decimal("6000"),
            },
            {
                "proyecto": "P-2",
                "project_name": "Beta",
                "cliente": "Globex",
                "billable_hours": Decimal("40.5"),
                "billable_amount_usd": Decimal("3040"),
            },
        ],
    }


@pytest.mark.asyncio
async def test_billable_hours_logged_ready_follows_talent_pattern(monkeypatch):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset("consultor_mensual", CONSULTOR_COLUMNS)
        },
        answers=_billable_answers(),
    )
    connects = install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(
        user_for(), months=2, top_n=5, as_of=AS_OF
    )

    assert result.status == "ready"
    assert result.supported is True
    assert result.proxy_note == fin.BILLABLE_HOURS_PROXY_NOTE
    assert "NO mide horas validadas" in result.proxy_note
    assert result.error is None
    assert result.billable_hours == 120.5
    assert result.billable_amount_usd == 9040.0
    assert result.projects_affected == 3
    assert result.contributors == 4
    assert [item["proyecto"] for item in result.top_projects] == ["P-1", "P-2"]
    assert result.top_projects[1]["billable_hours"] == 40.5
    assert result.window_start == date(2026, 8, 1)
    assert result.window_end == date(2026, 10, 1)

    # Talent pattern: dedicated connection, repeatable_read readonly, GUCs, close.
    assert connects()[0]["command_timeout"] == 10
    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert conn.scope == (TENANT_A, WORKSPACE_A)
    assert conn.closed is True

    totals_sql = conn.sql_for("finance.billable_hours_logged.totals")
    assert "workspace_id::text = $1 AND tenant_id::text = $2" in totals_sql
    assert '"omega_publication_gold"."run_' in totals_sql
    assert "gold_consultor_mensual" not in totals_sql  # never string-built
    assert "SUM(horas_facturables * billing_rate_usd)" in totals_sql
    assert "COUNT(DISTINCT consultor)" in totals_sql
    assert "LIMIT" not in totals_sql
    assert conn.args_for("finance.billable_hours_logged.totals") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 8, 1),
        date(2026, 10, 1),
    )
    top_sql = conn.sql_for("finance.billable_hours_logged.top_projects")
    assert "GROUP BY proyecto" in top_sql
    assert "ORDER BY billable_amount_usd DESC" in top_sql
    assert "LIMIT $5" in top_sql
    assert conn.args_for("finance.billable_hours_logged.top_projects")[-1] == 5

    evidence = result.evidence_refs[0]
    assert evidence["type"] == "gold_relation"
    assert evidence["dataset"] == "consultor_mensual"
    assert evidence["relation"].startswith('"omega_publication_gold"."run_')
    assert evidence["generation"] == 3
    assert evidence["published_at"] == PUBLISHED_AT
    assert evidence["filters"]["months"] == 2

    payload = result.to_dict()
    assert payload["window_start"] == "2026-08-01"
    assert payload["evidence_refs"][0]["published_at"] == PUBLISHED_AT.isoformat()
    assert payload["supported"] is True


@pytest.mark.asyncio
async def test_billable_hours_logged_degrades_without_rate_column(monkeypatch):
    answers = _billable_answers()
    answers["finance.billable_hours_logged.totals"]["billable_amount_usd"] = None
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset(
                "consultor_mensual", _without(CONSULTOR_COLUMNS, "billing_rate_usd")
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), top_n=5, as_of=AS_OF)

    assert result.status == "degraded"
    assert result.billable_amount_usd is None
    assert result.billable_hours == 120.5
    assert result.missing_columns == ["consultor_mensual.billing_rate_usd"]
    assert any("billing_rate_usd ausente" in note for note in result.notes)
    totals_sql = conn.sql_for("finance.billable_hours_logged.totals")
    assert "NULL::float8 AS billable_amount_usd" in totals_sql
    assert "billing_rate_usd" not in totals_sql
    assert "ORDER BY billable_hours DESC" in conn.sql_for(
        "finance.billable_hours_logged.top_projects"
    )
    assert result.evidence_refs[0]["missing_optional_columns"] == ["billing_rate_usd"]


@pytest.mark.asyncio
async def test_billable_hours_logged_unavailable_without_published_head(monkeypatch):
    conn = FakeConn(datasets={}, answers=_billable_answers())
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error == "missing: dataset unavailable: consultor_mensual"
    assert result.billable_hours is None
    assert result.proxy_note == fin.BILLABLE_HOURS_PROXY_NOTE
    assert conn.markers() == []  # no aggregate ran
    assert conn.closed is True


@pytest.mark.asyncio
async def test_billable_hours_logged_unavailable_when_relation_does_not_exist(
    monkeypatch,
):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset(
                "consultor_mensual", CONSULTOR_COLUMNS, regclass=False
            )
        },
        answers=_billable_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error.startswith("missing:")
    assert conn.markers() == []


@pytest.mark.asyncio
async def test_billable_hours_logged_invalid_schema_when_required_column_missing(
    monkeypatch,
):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset(
                "consultor_mensual", _without(CONSULTOR_COLUMNS, "horas_facturables")
            )
        },
        answers=_billable_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error.startswith("invalid_schema: consultor_mensual")
    assert result.missing_columns == ["horas_facturables"]
    assert conn.markers() == []


@pytest.mark.asyncio
async def test_billable_hours_logged_requires_tenant_scope(monkeypatch):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset("consultor_mensual", CONSULTOR_COLUMNS)
        },
        answers=_billable_answers(),
    )
    connects = install_gold_connect(monkeypatch, conn)

    no_tenant = {"workspace_id": WORKSPACE_A, "active_workspace_id": WORKSPACE_A}
    result = await fin.query_billable_hours_logged(no_tenant, as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error.startswith("no_permission:")
    assert connects() == []  # never connected

    no_workspace = {"tenant_id": TENANT_A, "active_tenant_id": TENANT_A}
    result = await fin.query_billable_hours_logged(no_workspace, as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error.startswith("invalid_scope:")
    assert connects() == []


@pytest.mark.asyncio
async def test_billable_hours_logged_bounds_top_n_and_months(monkeypatch):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset("consultor_mensual", CONSULTOR_COLUMNS)
        },
        answers=_billable_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(
        user_for(), months=99, top_n=500, as_of=AS_OF
    )

    assert result.months == 24
    assert result.window_start == date(2024, 10, 1)
    # Named rows are the controlled exception: clamped to MAX_NAMED_ROWS (10).
    assert (
        conn.args_for("finance.billable_hours_logged.top_projects")[-1]
        == MAX_NAMED_ROWS
    )


@pytest.mark.asyncio
async def test_billable_hours_logged_is_aggregates_only_by_default(monkeypatch):
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset("consultor_mensual", CONSULTOR_COLUMNS)
        },
        answers=_billable_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), as_of=AS_OF)

    assert result.status == "ready"
    assert result.billable_hours == 120.5
    assert result.top_projects == []
    assert "finance.billable_hours_logged.top_projects" not in conn.markers()


@pytest.mark.asyncio
async def test_billable_hours_logged_postgres_error_becomes_unavailable(monkeypatch):
    answers = _billable_answers()
    answers["finance.billable_hours_logged.totals"] = asyncpg.PostgresError("boom")
    conn = FakeConn(
        datasets={
            "consultor_mensual": _dataset("consultor_mensual", CONSULTOR_COLUMNS)
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_billable_hours_logged(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error.startswith("unavailable: boom")
    assert conn.closed is True


# ── F3 labor cost by department ─────────────────────────────────────────────


def _labor_answers() -> dict:
    return {
        "finance.labor_cost_by_department.period": date(2026, 8, 1),
        "finance.labor_cost_by_department.totals": {
            "departments_count": 3,
            "headcount": 12,
            "total_hours": Decimal("1800"),
            "total_cost": Decimal("54000"),
            "total_sunk_cost": Decimal("6000"),
            "total_potential_cost": Decimal("60000"),
        },
        "finance.labor_cost_by_department.departments": [
            {
                "departamento": "SAP",
                "headcount": 7,
                "hours": Decimal("1100"),
                "cost": Decimal("33000"),
                "sunk_cost": Decimal("3000"),
                "potential_cost": Decimal("36000"),
            },
            {
                "departamento": "Data",
                "headcount": 5,
                "hours": Decimal("700"),
                "cost": Decimal("21000"),
                "sunk_cost": Decimal("3000"),
                "potential_cost": Decimal("24000"),
            },
        ],
    }


@pytest.mark.asyncio
async def test_labor_cost_by_department_ready(monkeypatch):
    conn = FakeConn(
        datasets={
            "costo_consultor_mensual": _dataset(
                "costo_consultor_mensual", COSTO_COLUMNS
            )
        },
        answers=_labor_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_labor_cost_by_department(user_for(), as_of=AS_OF)

    assert result.status == "ready"
    assert result.proxy_note == fin.LABOR_COST_PROXY_NOTE
    assert "NO es nomina" in result.proxy_note
    assert result.period == date(2026, 8, 1)
    assert result.departments_count == 3
    assert result.headcount == 12
    assert result.total_cost == 54000.0
    assert result.total_sunk_cost == 6000.0
    assert [item["departamento"] for item in result.departments] == ["SAP", "Data"]
    assert result.departments[0]["cost"] == 33000.0
    assert any("2 departamentos con mayor costo de 3" in note for note in result.notes)

    assert conn.args_for("finance.labor_cost_by_department.period") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 9, 1),
    )
    assert "mes::date < $3::date" in conn.sql_for(
        "finance.labor_cost_by_department.period"
    )
    assert conn.args_for("finance.labor_cost_by_department.totals") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 8, 1),
    )
    departments_sql = conn.sql_for("finance.labor_cost_by_department.departments")
    assert "GROUP BY departamento" in departments_sql
    assert "COUNT(DISTINCT userid)" in departments_sql
    assert "nombre_completo" not in departments_sql  # never PII
    assert conn.args_for("finance.labor_cost_by_department.departments")[-1] == 20
    assert result.evidence_refs[0]["filters"]["period"] == "2026-08-01"


@pytest.mark.asyncio
async def test_labor_cost_by_department_degrades_without_closed_period(monkeypatch):
    answers = _labor_answers()
    answers["finance.labor_cost_by_department.period"] = None
    conn = FakeConn(
        datasets={
            "costo_consultor_mensual": _dataset(
                "costo_consultor_mensual", COSTO_COLUMNS
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_labor_cost_by_department(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.period is None
    assert result.total_cost is None
    assert result.departments == []
    assert any("sin meses cerrados" in note for note in result.notes)
    assert conn.markers() == ["finance.labor_cost_by_department.period"]


@pytest.mark.asyncio
async def test_labor_cost_by_department_degrades_without_optional_columns(monkeypatch):
    answers = _labor_answers()
    answers["finance.labor_cost_by_department.totals"].update(
        {"headcount": None, "total_hours": None}
    )
    conn = FakeConn(
        datasets={
            "costo_consultor_mensual": _dataset(
                "costo_consultor_mensual",
                _without(COSTO_COLUMNS, "userid", "horas_ejecutadas"),
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_labor_cost_by_department(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.headcount is None
    assert result.total_hours is None
    assert result.total_cost == 54000.0
    assert result.missing_columns == [
        "costo_consultor_mensual.horas_ejecutadas",
        "costo_consultor_mensual.userid",
    ]
    totals_sql = conn.sql_for("finance.labor_cost_by_department.totals")
    assert "userid" not in totals_sql and "horas_ejecutadas" not in totals_sql


@pytest.mark.asyncio
async def test_labor_cost_by_department_unavailable_without_head(monkeypatch):
    conn = FakeConn(datasets={}, answers=_labor_answers())
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_labor_cost_by_department(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error == "missing: dataset unavailable: costo_consultor_mensual"


# ── F4 project margin ───────────────────────────────────────────────────────


def _margin_answers() -> dict:
    project = {
        "proyecto": "P-1",
        "project_name": "Alpha",
        "cliente": "ACME",
        "tipo_proyecto": "T&M",
        "revenue_base": Decimal("100000"),
        "cost_direct": Decimal("60000"),
        "cost_sunk": Decimal("5000"),
        "margin": Decimal("35000"),
        "margin_pct": Decimal("35"),
        "billable_hours": Decimal("900"),
    }
    loser = {
        **project,
        "proyecto": "P-9",
        "project_name": "Omega",
        "revenue_base": Decimal("10000"),
        "cost_direct": Decimal("14000"),
        "cost_sunk": Decimal("1000"),
        "margin": Decimal("-5000"),
        "margin_pct": Decimal("-50"),
    }
    return {
        "finance.project_margin.totals": {
            "projects_count": 4,
            "total_revenue_base": Decimal("250000"),
            "total_cost_direct": Decimal("150000"),
            "total_cost_sunk": Decimal("12000"),
            "total_margin": Decimal("88000"),
            "total_margin_pct": Decimal("35.2"),
            "total_original_billing": Decimal("240000"),
            "original_currencies": ["MXN", "USD"],
        },
        "finance.project_margin.top_projects": [project],
        "finance.project_margin.bottom_projects": [loser],
    }


@pytest.mark.asyncio
async def test_project_margin_ready(monkeypatch):
    conn = FakeConn(
        datasets={"pnl_mensual": _dataset("pnl_mensual", PNL_COLUMNS)},
        answers=_margin_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_project_margin(user_for(), months=3, top_n=5, as_of=AS_OF)

    assert result.status == "ready"
    assert result.proxy_note == fin.PROJECT_MARGIN_PROXY_NOTE
    assert "SIN verificar" in result.proxy_note
    assert result.projects_count == 4
    assert result.total_revenue_base == 250000.0
    assert result.total_cost_direct == 150000.0
    assert result.total_cost_sunk == 12000.0
    assert result.total_margin == 88000.0
    assert result.total_margin_pct == 35.2
    assert result.total_original_billing == 240000.0
    assert result.original_currencies == ["MXN", "USD"]
    assert result.top_projects[0]["proyecto"] == "P-1"
    assert result.top_projects[0]["margin"] == 35000.0
    assert result.bottom_projects[0]["proyecto"] == "P-9"
    assert result.bottom_projects[0]["margin"] == -5000.0
    assert result.window_start == date(2026, 7, 1)
    assert result.window_end == date(2026, 10, 1)

    totals_sql = conn.sql_for("finance.project_margin.totals")
    assert "COALESCE(SUM(cost_sunk_base_amount), 0)" in totals_sql
    assert "array_agg(DISTINCT original_currency)" in totals_sql
    assert "SUM(original_billing_amount)" in totals_sql
    assert "revenue_usd" not in totals_sql  # always NULL in the dataset, never used
    assert "ORDER BY margin DESC" in conn.sql_for("finance.project_margin.top_projects")
    assert "ORDER BY margin ASC" in conn.sql_for(
        "finance.project_margin.bottom_projects"
    )
    assert conn.args_for("finance.project_margin.top_projects") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 7, 1),
        date(2026, 10, 1),
        5,
    )
    filters = result.evidence_refs[0]["filters"]
    assert filters["original_currencies"] == ["MXN", "USD"]
    assert "unverified" in filters["base_currency"]


@pytest.mark.asyncio
async def test_project_margin_degrades_without_sunk_cost_and_currency(monkeypatch):
    answers = _margin_answers()
    answers["finance.project_margin.totals"].update(
        {"total_cost_sunk": None, "original_currencies": None}
    )
    conn = FakeConn(
        datasets={
            "pnl_mensual": _dataset(
                "pnl_mensual",
                _without(PNL_COLUMNS, "cost_sunk_base_amount", "original_currency"),
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_project_margin(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.total_cost_sunk is None
    assert result.original_currencies == []
    assert result.missing_columns == [
        "pnl_mensual.cost_sunk_base_amount",
        "pnl_mensual.original_currency",
    ]
    totals_sql = conn.sql_for("finance.project_margin.totals")
    assert "cost_sunk_base_amount" not in totals_sql
    assert "NULL::text[] AS original_currencies" in totals_sql
    assert " - 0)" in totals_sql


@pytest.mark.asyncio
async def test_project_margin_invalid_schema_without_revenue(monkeypatch):
    conn = FakeConn(
        datasets={
            "pnl_mensual": _dataset(
                "pnl_mensual", _without(PNL_COLUMNS, "revenue_base_amount")
            )
        },
        answers=_margin_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await fin.query_project_margin(user_for(), as_of=AS_OF)

    assert result.status == "unavailable"
    assert result.error.startswith("invalid_schema: pnl_mensual")
    assert result.missing_columns == ["revenue_base_amount"]
    assert conn.markers() == []

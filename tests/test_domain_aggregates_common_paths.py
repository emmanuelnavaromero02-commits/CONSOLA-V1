from __future__ import annotations

from datetime import date, datetime, timezone

import asyncpg
import pytest

from app.services.intelligence import (
    domain_aggregate_support as support,
    finance_aggregates as fin,
    operations_aggregates as ops,
    risk_aggregates as risk,
)
from tests.domain_aggregate_fakes import (
    TENANT_A,
    WORKSPACE_A,
    WORKSPACE_B,
    FakeConn,
    FakeGoldDataset,
    install_console_pool,
    install_gold_connect,
    user_for,
)

AS_OF = date(2026, 9, 13)
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

GOLD_CASES = [
    (
        fin.query_billable_hours_logged,
        {"as_of": AS_OF},
        fin.CONSULTOR_MENSUAL_DATASET,
        ["mes", "proyecto", "horas_facturables", "billing_rate_usd"],
        "horas_facturables",
        "finance.billable_hours_logged.totals",
    ),
    (
        fin.query_labor_cost_by_department,
        {"as_of": AS_OF},
        fin.COSTO_CONSULTOR_DATASET,
        ["mes", "departamento", "costo_ejecutado"],
        "costo_ejecutado",
        "finance.labor_cost_by_department.period",
    ),
    (
        fin.query_project_margin,
        {"as_of": AS_OF},
        fin.PNL_MENSUAL_DATASET,
        ["mes", "proyecto", "revenue_base_amount", "cost_direct_base_amount"],
        "revenue_base_amount",
        "finance.project_margin.totals",
    ),
    (
        ops.query_absence_rate_company_by_type,
        {"as_of": AS_OF},
        ops.ABSENCE_DATASET,
        ["absence_month", "absence_type", "total_days_workable"],
        "total_days_workable",
        "operations.absence_rate.period",
    ),
    (
        risk.query_attrition_risk_population,
        {},
        risk.RETENTION_RISK_DATASET,
        ["risk_band", "retention_risk_score", "department_name"],
        "risk_band",
        "risk.attrition_risk_population.bands",
    ),
    (
        risk.query_employment_end_expiry,
        {"as_of": AS_OF},
        risk.EMPLOYEE_360_DATASET,
        ["end_date", "is_active", "department_name"],
        "end_date",
        "risk.employment_end_expiry.windows",
    ),
    (
        risk.query_deal_slippage,
        {"as_of": AS_OF},
        risk.DEALS_AT_RISK_DATASET,
        ["close_date", "amount", "stage_name", "motivo_riesgo"],
        "close_date",
        "risk.deal_slippage.totals",
    ),
]
GOLD_IDS = [case[0].__name__ for case in GOLD_CASES]


def _assert_unavailable(result, prefix: str) -> None:
    assert result.status == support.STATUS_UNAVAILABLE
    assert result.supported is True
    assert result.error is not None and result.error.startswith(prefix), result.error
    assert result.evidence_refs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_no_head_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    conn = FakeConn(datasets={}, answers={})
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, f"missing: dataset unavailable: {dataset}")
    assert conn.markers() == []
    assert conn.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_relation_absent_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    conn = FakeConn(
        datasets={dataset: FakeGoldDataset(dataset, columns, regclass=False)},
        answers={},
    )
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "missing:")
    assert conn.markers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_head_not_published_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    conn = FakeConn(
        datasets={
            dataset: FakeGoldDataset(dataset, columns, status="recoverable_failed")
        },
        answers={},
    )
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "missing:")
    assert conn.markers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_required_column_missing_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    remaining = [column for column in columns if column != required]
    conn = FakeConn(datasets={dataset: FakeGoldDataset(dataset, remaining)}, answers={})
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, f"invalid_schema: {dataset}")
    assert result.missing_columns == [required]
    assert conn.markers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_scope_columns_missing_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    dataset_obj = FakeGoldDataset(dataset, columns)
    dataset_obj.columns.pop("tenant_id")
    dataset_obj.columns.pop("workspace_id")
    conn = FakeConn(datasets={dataset: dataset_obj}, answers={})
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "invalid_schema:")
    assert set(result.missing_columns) == {"tenant_id", "workspace_id"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_other_workspace_head_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    conn = FakeConn(
        datasets={dataset: FakeGoldDataset(dataset, columns, workspace_id=WORKSPACE_B)},
        answers={},
    )
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(TENANT_A, WORKSPACE_A), **kwargs)
    _assert_unavailable(result, "missing:")
    assert conn.scope == (TENANT_A, WORKSPACE_A)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_postgres_error_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    conn = FakeConn(
        datasets={dataset: FakeGoldDataset(dataset, columns)},
        answers={marker: asyncpg.PostgresError("relation vanished")},
    )
    install_gold_connect(monkeypatch, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "unavailable: relation vanished")
    assert conn.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_scope_is_required_before_connecting(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    connects = install_gold_connect(monkeypatch, FakeConn())
    result = await fn(
        {"workspace_id": WORKSPACE_A, "active_workspace_id": WORKSPACE_A}, **kwargs
    )
    _assert_unavailable(result, "no_permission:")
    result = await fn({"tenant_id": TENANT_A, "active_tenant_id": TENANT_A}, **kwargs)
    _assert_unavailable(result, "invalid_scope:")
    assert connects() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn, kwargs, dataset, columns, required, marker", GOLD_CASES, ids=GOLD_IDS
)
async def test_gold_missing_dsn_is_unavailable(
    monkeypatch, fn, kwargs, dataset, columns, required, marker
):
    monkeypatch.delenv("GOLD_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "unavailable: gold database unavailable")


@pytest.mark.parametrize(
    "value, expected",
    [
        (float("inf"), support.DEFAULT_TOP_N),
        (float("-inf"), support.DEFAULT_TOP_N),
        (float("nan"), support.DEFAULT_TOP_N),
        (True, support.DEFAULT_TOP_N),
        ("x", support.DEFAULT_TOP_N),
        (None, support.DEFAULT_TOP_N),
        (0, 1),
        (-7, 1),
        (10**40, support.MAX_GROUP_ROWS),
        ("7", 7),
    ],
)
def test_clamp_top_n_never_raises(value, expected):
    assert support.clamp_top_n(value) == expected


def test_clamp_months_never_raises():
    assert support.clamp_months(float("inf")) == 1
    assert support.clamp_months(True) == 1
    assert support.clamp_months(0) == 1
    assert support.clamp_months(99) == 24
    assert support.clamp_months("3") == 3


@pytest.mark.asyncio
async def test_infinite_top_n_degrades_instead_of_raising(monkeypatch):
    conn = FakeConn(datasets={}, answers={})
    install_gold_connect(monkeypatch, conn)
    result = await risk.query_deal_slippage(user_for(), top_n=float("inf"), as_of=AS_OF)
    assert result.status == support.STATUS_UNAVAILABLE
    result = await fin.query_billable_hours_logged(
        user_for(), months=float("inf"), as_of=AS_OF
    )
    assert result.status == support.STATUS_UNAVAILABLE
    assert result.months == 1


CONSOLE_CASES = [
    (ops.query_pipeline_health, {"as_of": NOW}, "operations.pipeline_health.totals"),
    (
        ops.query_data_freshness_by_cartridge,
        {"as_of": NOW, "sla_hours": 24},
        "operations.data_freshness.totals",
    ),
]
CONSOLE_IDS = [case[0].__name__ for case in CONSOLE_CASES]


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs, marker", CONSOLE_CASES, ids=CONSOLE_IDS)
async def test_console_db_error_is_unavailable(monkeypatch, fn, kwargs, marker):
    conn = FakeConn(answers={marker: asyncpg.PostgresError("deadlock")})
    install_console_pool(monkeypatch, ops, conn)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "unavailable: deadlock")
    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs, marker", CONSOLE_CASES, ids=CONSOLE_IDS)
async def test_console_requires_workspace_before_acquiring(
    monkeypatch, fn, kwargs, marker
):
    conn = FakeConn(answers={})
    pool = install_console_pool(monkeypatch, ops, conn)
    result = await fn({"tenant_id": TENANT_A, "active_tenant_id": TENANT_A}, **kwargs)
    _assert_unavailable(result, "no_permission: active workspace is required")
    assert pool.acquired == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fn, kwargs, marker", CONSOLE_CASES, ids=CONSOLE_IDS)
async def test_console_pool_failure_is_unavailable(monkeypatch, fn, kwargs, marker):
    async def broken_pool():
        raise OSError("postgres unreachable")

    monkeypatch.setattr(ops.auth, "pool", broken_pool)
    result = await fn(user_for(), **kwargs)
    _assert_unavailable(result, "unavailable: postgres unreachable")

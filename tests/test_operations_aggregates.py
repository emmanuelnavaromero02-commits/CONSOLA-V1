"""Operations aggregates — unit tests over the fake asyncpg plumbing.

pipeline_health / data_freshness run against the console DB (pool +
scoped_db); absence_rate runs against Gold (publication heads). Covers scope
enforcement, merging of pipeline_runs + extraction_runs, SLA resolution
(param > env > default), degraded/unavailable paths and bounded top-N.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.intelligence import operations_aggregates as ops
from app.services.intelligence.domain_aggregate_support import MAX_GROUP_ROWS
from tests.domain_aggregate_fakes import (
    TENANT_A,
    WORKSPACE_A,
    FakeConn,
    FakeGoldDataset,
    install_console_pool,
    install_gold_connect,
    user_for,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
AS_OF = NOW.date()

ABSENCE_COLUMNS = {
    "absence_month": "date",
    "absence_type": "text",
    "total_days_workable": "numeric",
    "employees_affected": "bigint",
    "absence_records": "bigint",
}
HEADCOUNT_COLUMNS = {
    "org_id": "text",
    "org_name": "text",
    "headcount": "bigint",
    "snapshot_month": "date",
}


# ── O1 pipeline health (console DB) ─────────────────────────────────────────


def _health_answers() -> dict:
    return {
        "operations.pipeline_health.pipeline_runs": [
            {
                "cartridge_id": "sap_successfactors",
                "failed_24h": 2,
                "success_24h": 5,
                "failed_7d": 4,
                "success_7d": 30,
                "partial_7d": 1,
                "last_failed_at": NOW - timedelta(hours=3),
            },
            {
                "cartridge_id": "replicon",
                "failed_24h": 0,
                "success_24h": 1,
                "failed_7d": 0,
                "success_7d": 7,
                "partial_7d": 0,
                "last_failed_at": None,
            },
        ],
        "operations.pipeline_health.extraction_runs": [
            {
                "cartridge_id": "sap_successfactors",
                "failed_24h": 1,
                "success_24h": 5,
                "failed_7d": 2,
                "success_7d": 30,
                "partial_7d": 0,
                "last_failed_at": NOW - timedelta(hours=1),
            },
            {
                "cartridge_id": "sap_hcm",
                "failed_24h": 3,
                "success_24h": 0,
                "failed_7d": 6,
                "success_7d": 2,
                "partial_7d": 0,
                "last_failed_at": NOW - timedelta(hours=2),
            },
        ],
        "operations.pipeline_health.recent_failures.pipeline_runs": [
            {
                "cartridge_id": "sap_successfactors",
                "source_ref": "sf_extract",
                "entity": "EmpJob",
                "finished_at": NOW - timedelta(hours=3),
                "started_at": NOW - timedelta(hours=4),
                "error_message": "timeout",
            }
        ],
        "operations.pipeline_health.recent_failures.extraction_runs": [
            {
                "cartridge_id": "sap_hcm",
                "source_ref": "incremental",
                "entity": "PA0001",
                "finished_at": None,
                "started_at": NOW - timedelta(hours=2),
                "error_message": "401 unauthorized",
            }
        ],
    }


@pytest.mark.asyncio
async def test_pipeline_health_merges_both_run_tables(monkeypatch):
    conn = FakeConn(answers=_health_answers())
    pool = install_console_pool(monkeypatch, ops, conn)

    result = await ops.query_pipeline_health(user_for(), as_of=NOW)

    assert result.status == "ready"
    assert result.supported is True
    assert result.proxy_note is None
    by_cartridge = {item["cartridge_id"]: item for item in result.cartridges}
    assert by_cartridge["sap_successfactors"]["failed_24h"] == 3
    assert by_cartridge["sap_successfactors"]["failed_7d"] == 6
    assert by_cartridge["sap_successfactors"]["success_7d"] == 60
    assert by_cartridge["sap_successfactors"]["failure_rate_7d"] == round(6 / 66, 4)
    assert by_cartridge["sap_successfactors"]["last_failed_at"] == NOW - timedelta(
        hours=1
    )
    assert by_cartridge["sap_successfactors"]["sources"] == [
        "pipeline_runs",
        "extraction_runs",
    ]
    assert by_cartridge["replicon"]["failure_rate_7d"] == 0.0
    assert by_cartridge["sap_hcm"]["failed_7d"] == 6
    assert [item["cartridge_id"] for item in result.cartridges][:2] == [
        "sap_hcm",
        "sap_successfactors",
    ]
    assert result.totals == {
        "failed_24h": 6,
        "success_24h": 11,
        "failed_7d": 12,
        "success_7d": 69,
        "partial_7d": 1,
    }
    assert result.cartridges_with_failures_24h == 2
    assert [item["cartridge_id"] for item in result.recent_failures] == [
        "sap_hcm",
        "sap_successfactors",
    ]
    assert result.recent_failures[0]["finished_at"] == NOW - timedelta(hours=2)

    # console scope: pool, repeatable_read readonly, GUCs via scoped_db, uuid predicate
    assert pool.acquired == 1
    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert conn.scope == (TENANT_A, WORKSPACE_A)
    sql = conn.sql_for("operations.pipeline_health.pipeline_runs")
    assert (
        "workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)" in sql
    )
    assert "GROUP BY cartridge_id" in sql
    assert "COUNT(*) FILTER (WHERE status = 'failed'  AND started_at >= $3)" in sql
    assert conn.args_for("operations.pipeline_health.pipeline_runs") == (
        WORKSPACE_A,
        TENANT_A,
        NOW - timedelta(hours=24),
        NOW - timedelta(days=7),
        MAX_GROUP_ROWS,
    )
    assert (
        conn.args_for("operations.pipeline_health.recent_failures.extraction_runs")[-1]
        == 10
    )
    assert {ref["table"] for ref in result.evidence_refs} == {
        "pipeline_runs",
        "extraction_runs",
    }
    assert result.evidence_refs[0]["database"] == "DATABASE_URL"
    assert result.to_dict()["as_of"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_pipeline_health_allows_missing_tenant_like_proactive(monkeypatch):
    conn = FakeConn(answers=_health_answers())
    install_console_pool(monkeypatch, ops, conn)

    workspace_only = {"workspace_id": WORKSPACE_A, "active_workspace_id": WORKSPACE_A}
    result = await ops.query_pipeline_health(workspace_only, as_of=NOW)

    assert result.status == "ready"
    assert conn.scope == ("", WORKSPACE_A)
    assert conn.args_for("operations.pipeline_health.pipeline_runs")[:2] == (
        WORKSPACE_A,
        None,
    )


@pytest.mark.asyncio
async def test_pipeline_health_requires_workspace(monkeypatch):
    conn = FakeConn(answers=_health_answers())
    pool = install_console_pool(monkeypatch, ops, conn)

    result = await ops.query_pipeline_health({"tenant_id": TENANT_A}, as_of=NOW)

    assert result.status == "unavailable"
    assert result.error == "no_permission: active workspace is required"
    assert pool.acquired == 0


@pytest.mark.asyncio
async def test_pipeline_health_db_error_becomes_unavailable(monkeypatch):
    answers = _health_answers()
    answers["operations.pipeline_health.pipeline_runs"] = OSError("connection reset")
    conn = FakeConn(answers=answers)
    install_console_pool(monkeypatch, ops, conn)

    result = await ops.query_pipeline_health(user_for(), as_of=NOW)

    assert result.status == "unavailable"
    assert result.error == "unavailable: connection reset"


# ── O2 data freshness ───────────────────────────────────────────────────────


def test_resolve_sla_hours_precedence(monkeypatch):
    monkeypatch.delenv(ops.FRESHNESS_SLA_ENV, raising=False)
    assert ops.resolve_sla_hours(None) == (24.0, "default")
    assert ops.resolve_sla_hours(6) == (6.0, "param")
    monkeypatch.setenv(ops.FRESHNESS_SLA_ENV, "48")
    assert ops.resolve_sla_hours(None) == (48.0, "env")
    assert ops.resolve_sla_hours(12) == (12.0, "param")
    monkeypatch.setenv(ops.FRESHNESS_SLA_ENV, "not-a-number")
    assert ops.resolve_sla_hours(None) == (24.0, "default")
    monkeypatch.setenv(ops.FRESHNESS_SLA_ENV, "-5")
    assert ops.resolve_sla_hours(0) == (24.0, "default")


def _freshness_answers() -> dict:
    return {
        "operations.data_freshness.extraction_runs": [
            {
                "cartridge_id": "replicon",
                "last_success_at": NOW - timedelta(hours=30),
                "success_runs": 40,
            },
            {
                "cartridge_id": "sap_hcm",
                "last_success_at": NOW - timedelta(hours=2),
                "success_runs": 9,
            },
        ],
        "operations.data_freshness.pipeline_runs": [
            {
                "cartridge_id": "replicon",
                "last_success_at": NOW - timedelta(hours=50),
                "success_runs": 3,
            },
            {
                "cartridge_id": "sap_successfactors",
                "last_success_at": NOW - timedelta(hours=100),
                "success_runs": 12,
            },
        ],
    }


@pytest.mark.asyncio
async def test_data_freshness_by_cartridge_with_param_sla(monkeypatch):
    monkeypatch.delenv(ops.FRESHNESS_SLA_ENV, raising=False)
    conn = FakeConn(answers=_freshness_answers())
    install_console_pool(monkeypatch, ops, conn)

    result = await ops.query_data_freshness_by_cartridge(
        user_for(), sla_hours=24, as_of=NOW
    )

    assert result.status == "ready"
    assert result.sla_hours == 24.0
    assert result.sla_source == "param"
    assert result.proxy_note == ops.FRESHNESS_PROXY_NOTE
    by_cartridge = {item["cartridge_id"]: item for item in result.cartridges}
    assert by_cartridge["replicon"]["hours_since_success"] == 30.0  # max across tables
    assert by_cartridge["replicon"]["success_runs"] == 43
    assert by_cartridge["replicon"]["exceeds_sla"] is True
    assert by_cartridge["sap_hcm"]["exceeds_sla"] is False
    assert by_cartridge["sap_successfactors"]["hours_since_success"] == 100.0
    assert [item["cartridge_id"] for item in result.cartridges] == [
        "sap_successfactors",
        "replicon",
        "sap_hcm",
    ]
    assert result.exceeding_sla == 2
    assert result.cartridges_count == 3
    sql = conn.sql_for("operations.data_freshness.extraction_runs")
    assert "status = 'success'" in sql
    assert (
        "workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)" in sql
    )
    assert conn.args_for("operations.data_freshness.extraction_runs") == (
        WORKSPACE_A,
        TENANT_A,
        MAX_GROUP_ROWS,
    )
    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]


@pytest.mark.asyncio
async def test_data_freshness_default_sla_is_degraded_and_env_wins(monkeypatch):
    monkeypatch.delenv(ops.FRESHNESS_SLA_ENV, raising=False)
    conn = FakeConn(answers=_freshness_answers())
    install_console_pool(monkeypatch, ops, conn)

    result = await ops.query_data_freshness_by_cartridge(user_for(), as_of=NOW)
    assert result.status == "degraded"
    assert result.sla_source == "default"
    assert any("default de 24h" in note for note in result.notes)

    monkeypatch.setenv(ops.FRESHNESS_SLA_ENV, "72")
    conn = FakeConn(answers=_freshness_answers())
    install_console_pool(monkeypatch, ops, conn)
    result = await ops.query_data_freshness_by_cartridge(user_for(), as_of=NOW)
    assert result.status == "ready"
    assert result.sla_source == "env"
    assert result.sla_hours == 72.0
    assert result.exceeding_sla == 1  # only sap_successfactors (100h)


# ── O3 absence rate (Gold) ──────────────────────────────────────────────────


def _absence_answers() -> dict:
    return {
        "operations.absence_rate.period": date(2026, 8, 1),
        "operations.absence_rate.totals": {
            "total_days_workable": Decimal("210"),
            "types_count": 2,
        },
        "operations.absence_rate.by_type": [
            {
                "absence_type": "0100",
                "days_workable": Decimal("150"),
                "employees_affected": 40,
                "absence_records": 55,
            },
            {
                "absence_type": "0200",
                "days_workable": Decimal("60"),
                "employees_affected": 12,
                "absence_records": 14,
            },
        ],
        "operations.absence_rate.headcount": 100,
    }


@pytest.mark.asyncio
async def test_absence_rate_company_by_type_ready(monkeypatch):
    conn = FakeConn(
        datasets={
            "absence_by_type_and_month": FakeGoldDataset(
                "absence_by_type_and_month", ABSENCE_COLUMNS
            ),
            "headcount_by_department": FakeGoldDataset(
                "headcount_by_department", HEADCOUNT_COLUMNS
            ),
        },
        answers=_absence_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await ops.query_absence_rate_company_by_type(user_for(), as_of=AS_OF)

    assert result.status == "ready"
    assert result.proxy_note == ops.ABSENCE_RATE_PROXY_NOTE
    assert "NO esta desglosada por unidad organizativa" in result.proxy_note
    assert result.period == date(2026, 8, 1)
    assert result.working_days == 21  # August 2026 has 21 weekdays
    assert result.headcount == 100
    assert result.total_days_workable == 210.0
    assert result.absence_rate == round(210 / (100 * 21), 4)
    assert result.types_count == 2
    assert result.by_type[0]["absence_type"] == "0100"
    assert result.by_type[0]["rate"] == round(150 / 2100, 4)
    assert result.by_type[1]["employees_affected"] == 12

    assert conn.transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert conn.scope == (TENANT_A, WORKSPACE_A)
    assert conn.args_for("operations.absence_rate.period") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 9, 1),
    )
    assert conn.args_for("operations.absence_rate.totals") == (
        WORKSPACE_A,
        TENANT_A,
        date(2026, 8, 1),
    )
    assert conn.args_for("operations.absence_rate.by_type")[-1] == 20
    assert "GROUP BY absence_type" in conn.sql_for("operations.absence_rate.by_type")
    headcount_sql = conn.sql_for("operations.absence_rate.headcount")
    assert "SUM(headcount)" in headcount_sql
    assert "workspace_id::text = $1 AND tenant_id::text = $2" in headcount_sql
    assert [ref["dataset"] for ref in result.evidence_refs] == [
        "absence_by_type_and_month",
        "headcount_by_department",
    ]


@pytest.mark.asyncio
async def test_absence_rate_degrades_without_headcount_dataset(monkeypatch):
    conn = FakeConn(
        datasets={
            "absence_by_type_and_month": FakeGoldDataset(
                "absence_by_type_and_month", ABSENCE_COLUMNS
            )
        },
        answers=_absence_answers(),
    )
    install_gold_connect(monkeypatch, conn)

    result = await ops.query_absence_rate_company_by_type(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.headcount is None
    assert result.absence_rate is None
    assert result.total_days_workable == 210.0
    assert result.by_type[0]["rate"] is None
    assert any("headcount_by_department no disponible" in note for note in result.notes)
    assert "operations.absence_rate.headcount" not in conn.markers()


@pytest.mark.asyncio
async def test_absence_rate_degrades_without_optional_columns(monkeypatch):
    answers = _absence_answers()
    for row in answers["operations.absence_rate.by_type"]:
        row["employees_affected"] = None
        row["absence_records"] = None
    conn = FakeConn(
        datasets={
            "absence_by_type_and_month": FakeGoldDataset(
                "absence_by_type_and_month",
                {
                    k: v
                    for k, v in ABSENCE_COLUMNS.items()
                    if k == "absence_month"
                    or k in ("absence_type", "total_days_workable")
                },
            ),
            "headcount_by_department": FakeGoldDataset(
                "headcount_by_department", HEADCOUNT_COLUMNS
            ),
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)

    result = await ops.query_absence_rate_company_by_type(user_for(), as_of=AS_OF)

    assert result.status == "degraded"
    assert result.absence_rate == round(210 / 2100, 4)
    assert result.by_type[0]["employees_affected"] is None
    assert result.missing_columns == [
        "absence_by_type_and_month.absence_records",
        "absence_by_type_and_month.employees_affected",
    ]
    by_type_sql = conn.sql_for("operations.absence_rate.by_type")
    assert "SUM(employees_affected)" not in by_type_sql
    assert "NULL::bigint AS employees_affected" in by_type_sql


@pytest.mark.asyncio
async def test_absence_rate_unavailable_without_head_or_period(monkeypatch):
    conn = FakeConn(datasets={}, answers=_absence_answers())
    install_gold_connect(monkeypatch, conn)
    result = await ops.query_absence_rate_company_by_type(user_for(), as_of=AS_OF)
    assert result.status == "unavailable"
    assert result.error == "missing: dataset unavailable: absence_by_type_and_month"

    answers = _absence_answers()
    answers["operations.absence_rate.period"] = None
    conn = FakeConn(
        datasets={
            "absence_by_type_and_month": FakeGoldDataset(
                "absence_by_type_and_month", ABSENCE_COLUMNS
            )
        },
        answers=answers,
    )
    install_gold_connect(monkeypatch, conn)
    result = await ops.query_absence_rate_company_by_type(user_for(), as_of=AS_OF)
    assert result.status == "degraded"
    assert result.period is None
    assert conn.markers() == ["operations.absence_rate.period"]

"""SAP Business One finance aggregates over the fake asyncpg plumbing."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.intelligence import sap_b1_aggregates as b1
from tests.domain_aggregate_fakes import (
    TENANT_A,
    WORKSPACE_A,
    FakeConn,
    FakeGoldDataset,
    install_gold_connect,
    user_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 25)
AUGUST = date(2026, 8, 1)
PUBLISHED_AT = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)


def _dataset(name: str, columns: set[str] | frozenset[str]) -> FakeGoldDataset:
    return FakeGoldDataset(name, sorted(columns), published_at=PUBLISHED_AT)


def _conn(name: str, columns, answers: dict) -> FakeConn:
    return FakeConn(datasets={name: _dataset(name, columns)}, answers=answers)


def test_module_follows_the_aggregate_contract():
    source = (REPO_ROOT / "console/app/services/intelligence/sap_b1_aggregates.py").read_text(encoding="utf-8")
    assert "fetchall" not in source and ".format(" not in source and "SELECT *" not in source
    assert not re.search(r"\bLIMIT\s+\d", source)
    for match in re.finditer(r"\bLIMIT\s+(\S+)", source):
        assert match.group(1).startswith("$")
    assert 'f"gold_' not in source and "run_gold_aggregate(" in source and "GOLD_SCOPE_PREDICATE" in source
    for note in (b1.GROUP_MARGIN_PROXY_NOTE, b1.COMPANY_MARGIN_PROXY_NOTE, b1.CUSTOMER_MARGIN_PROXY_NOTE,
                 b1.ITEM_FAMILY_MARGIN_PROXY_NOTE, b1.BELOW_MIN_PROXY_NOTE, b1.RECONCILIATION_PROXY_NOTE,
                 b1.DATA_QUALITY_PROXY_NOTE):
        assert "NO " in note and len(note) > 80


@pytest.mark.parametrize(
    "dataset, columns",
    [
        (b1.CONSOLIDATED_DATASET, b1._CONSOLIDATED_REQUIRED),
        (b1.COMPANY_DATASET, b1._COMPANY_REQUIRED),
        (b1.CUSTOMER_DATASET, b1._CUSTOMER_REQUIRED),
        (b1.ITEM_DATASET, b1._ITEM_REQUIRED),
        (b1.RECONCILIATION_DATASET, b1._RECONCILIATION_REQUIRED),
        (b1.DATA_QUALITY_DATASET, b1._DATA_QUALITY_REQUIRED),
        (b1.SCORECARD_DATASET, b1._SCORECARD_REQUIRED),
        (b1.EXPIRY_DATASET, b1._EXPIRY_REQUIRED),
        (b1.COVERAGE_DATASET, b1._COVERAGE_REQUIRED),
    ],
)
def test_required_columns_are_real_outputs_of_the_gold_sql(dataset, columns):
    sql = (REPO_ROOT / "cartridges" / "sap_b1" / "datasets" / f"{dataset}.sql").read_text(encoding="utf-8")
    assert "(gold)" in sql.splitlines()[0]
    for column in columns:
        assert re.search(rf"\b{column}\b", sql), (dataset, column)


@pytest.mark.asyncio
async def test_group_margin_flags_a_drop_against_the_trailing_months(monkeypatch):
    months = [
        {"doc_month": date(2026, month, 1), "local_currency": "MXN", "revenue": Decimal("1000"),
         "external_gp": Decimal("300"), "consolidated_gp": Decimal(gp), "up_change": Decimal("5")}
        for month, gp in ((8, "300"), (7, "380"), (6, "390"), (5, "400"))
    ]
    conn = _conn(b1.CONSOLIDATED_DATASET, b1._CONSOLIDATED_REQUIRED, {"sap_b1.group_margin.months": months})
    install_gold_connect(monkeypatch, conn)

    result = await b1.query_group_margin(user_for(), as_of=AS_OF)

    assert result.status == "ready" and result.period == "2026-08" and result.currency == "MXN"
    assert result.consolidated_margin_pct == 30.0 and result.trailing_margin_pct == 39.0
    assert result.external_margin_pct == 30.0 and result.unrealized_profit_change == 5.0
    assert len(result.breaches) == 1 and "cayo 9.0 puntos" in result.breaches[0]
    sql = conn.sql_for("sap_b1.group_margin.months")
    assert "workspace_id::text = $1 AND tenant_id::text = $2" in sql and "LIMIT $4" in sql
    assert conn.args_for("sap_b1.group_margin.months")[:3] == (WORKSPACE_A, TENANT_A, date(2026, 9, 1))
    assert conn.scope == (TENANT_A, WORKSPACE_A) and conn.closed


@pytest.mark.asyncio
async def test_group_margin_without_closed_months_is_degraded_with_a_reason(monkeypatch):
    conn = _conn(b1.CONSOLIDATED_DATASET, b1._CONSOLIDATED_REQUIRED, {"sap_b1.group_margin.months": []})
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_group_margin(user_for(), as_of=AS_OF)
    assert result.status == "degraded" and result.notes == ["sin meses cerrados con datos"] and not result.breaches


@pytest.mark.asyncio
async def test_a_missing_dataset_or_column_is_unavailable(monkeypatch):
    install_gold_connect(monkeypatch, FakeConn(datasets={}, answers={}))
    assert (await b1.query_company_margin(user_for(), as_of=AS_OF)).status == "unavailable"
    conn = _conn(b1.COMPANY_DATASET, b1._COMPANY_REQUIRED - {"min_margin_pct"}, {})
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_company_margin(user_for(), as_of=AS_OF)
    assert result.status == "unavailable" and result.error.startswith("invalid_schema")


@pytest.mark.asyncio
async def test_company_margin_flags_companies_under_their_minimum(monkeypatch):
    conn = _conn(b1.COMPANY_DATASET, b1._COMPANY_REQUIRED, {
        "sap_b1.company_margin.closed_period": {"period": AUGUST},
        "sap_b1.company_margin.companies": [
            {"company": "empresa_a", "revenue": Decimal("1000"), "gross_profit": Decimal("200"), "min_margin_pct": Decimal("25")},
            {"company": "empresa_b", "revenue": Decimal("500"), "gross_profit": Decimal("150"), "min_margin_pct": Decimal("25")},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_company_margin(user_for(), as_of=AS_OF)
    assert [c["margin_pct"] for c in result.companies] == [20.0, 30.0]
    assert result.breaches == ["La empresa empresa_a cerro 2026-08 con margen de 20.0%, por debajo del minimo de 25.0%."]
    assert "scope = 'external'" in conn.sql_for("sap_b1.company_margin.companies")
    assert conn.args_for("sap_b1.company_margin.companies")[2] == AUGUST


@pytest.mark.asyncio
async def test_customer_names_only_travel_when_asked(monkeypatch):
    answers = {
        "sap_b1.customer_margin.closed_period": {"period": AUGUST},
        "sap_b1.customer_margin.totals": {"customers": 40, "below_min": 3, "negative": 1,
                                          "revenue": Decimal("1000"), "revenue_below_min": Decimal("120")},
        "sap_b1.customer_margin.worst": [
            {"company": "empresa_a", "card_name": "Cliente Uno", "revenue": Decimal("100"), "gross_profit": Decimal("-5")},
        ],
    }
    conn = _conn(b1.CUSTOMER_DATASET, b1._CUSTOMER_REQUIRED, answers)
    install_gold_connect(monkeypatch, conn)
    plain = await b1.query_customer_margin(user_for(), as_of=AS_OF)
    assert plain.worst_customers == [] and "sap_b1.customer_margin.worst" not in conn.markers()
    assert plain.revenue_below_min_pct == 12.0 and len(plain.breaches) == 2

    conn = _conn(b1.CUSTOMER_DATASET, b1._CUSTOMER_REQUIRED, answers)
    install_gold_connect(monkeypatch, conn)
    named = await b1.query_customer_margin(user_for(), as_of=AS_OF, top_n=99)
    assert named.worst_customers == [{"company": "empresa_a", "customer": "Cliente Uno", "revenue": 100.0, "margin_pct": -5.0}]
    assert conn.args_for("sap_b1.customer_margin.worst")[-1] == 10


@pytest.mark.asyncio
async def test_item_families_and_below_min_sales(monkeypatch):
    conn = FakeConn(
        datasets={b1.ITEM_DATASET: _dataset(b1.ITEM_DATASET, b1._ITEM_REQUIRED)},
        answers={
            "sap_b1.item_family_margin.closed_period": {"period": AUGUST},
            "sap_b1.item_family_margin.families": [
                {"item_group_name": "Terminado", "revenue": Decimal("900"), "gross_profit": Decimal("270"), "total_revenue": Decimal("1000")},
                {"item_group_name": "Servicios", "revenue": Decimal("100"), "gross_profit": Decimal("-10"), "total_revenue": Decimal("1000")},
            ],
            "sap_b1.below_min_sales.closed_period": {"period": AUGUST},
            "sap_b1.below_min_sales.totals": {"revenue": Decimal("1000"), "below_min": Decimal("150"), "below_cost": Decimal("25")},
        },
    )
    install_gold_connect(monkeypatch, conn)
    families = await b1.query_item_family_margin(user_for(), as_of=AS_OF)
    assert [(f["family"], f["mix_pct"], f["margin_pct"]) for f in families.families] == [("Terminado", 90.0, 30.0), ("Servicios", 10.0, -10.0)]
    assert families.breaches == ["La familia Servicios cerro 2026-08 con margen negativo."]
    below = await b1.query_below_min_sales(user_for(), as_of=AS_OF)
    assert (below.below_min_pct, below.below_cost_pct) == (15.0, 2.5) and len(below.breaches) == 1


@pytest.mark.asyncio
async def test_reconciliation_and_data_quality_raise_one_breach_per_finding(monkeypatch):
    conn = FakeConn(
        datasets={
            b1.RECONCILIATION_DATASET: _dataset(b1.RECONCILIATION_DATASET, b1._RECONCILIATION_REQUIRED),
            b1.DATA_QUALITY_DATASET: _dataset(b1.DATA_QUALITY_DATASET, b1._DATA_QUALITY_REQUIRED),
        },
        answers={
            "sap_b1.reconciliation.totals": {"company_months": 36, "within": 20, "outside": 1, "without": 15},
            "sap_b1.reconciliation.outliers": [
                {"company": "empresa_a", "doc_month": AUGUST, "revenue_diff_pct": Decimal("-1.9608"),
                 "cogs_diff_pct": Decimal("0"), "gross_profit_diff_pct": Decimal("-4.1"), "tolerance_pct": Decimal("1")},
            ],
            "sap_b1.data_quality.totals": {"checks": 24, "below": 1},
            "sap_b1.data_quality.failing": [
                {"company": "empresa_b", "check_code": "rfc_clientes_valido", "check_group": "maestros",
                 "total": 15, "failing": 1, "pct_ok": Decimal("93.33"), "min_pct": Decimal("95")},
            ],
        },
    )
    install_gold_connect(monkeypatch, conn)
    reco = await b1.query_reconciliation(user_for(), as_of=AS_OF)
    assert (reco.within_tolerance, reco.out_of_tolerance, reco.without_controls) == (20, 1, 15)
    assert reco.outliers[0]["period"] == "2026-08" and "fuera de tolerancia" in reco.breaches[0]
    assert conn.args_for("sap_b1.reconciliation.totals")[2:] == (date(2025, 9, 1), date(2026, 9, 1))
    quality = await b1.query_data_quality(user_for())
    assert quality.failing[0]["check"] == "rfc clientes valido"
    assert quality.breaches == ["Calidad de datos en empresa_b: rfc clientes valido al 93.33% (minimo 95.0%)."]


@pytest.mark.asyncio
async def test_scorecard_counts_colours_and_names_the_red_metrics(monkeypatch):
    def row(name, color, growth_color="verde"):
        return {"distributor": name, "sell_out_revenue_local": Decimal("100"), "sell_out_qty": Decimal("10"),
                "sell_in_qty": Decimal("12"), "growth_mom_pct": Decimal("1"), "growth_yoy_pct": Decimal("-8"),
                "sell_through_3m_pct": Decimal("30"), "channel_days": Decimal("45"), "margin_pct": Decimal("22"),
                "expiry_exposed_pct": None, "growth_color": growth_color, "sell_through_color": "verde",
                "channel_days_color": "verde", "margin_color": "verde", "expiry_color": "sin_umbral", "overall_color": color}
    conn = _conn(b1.SCORECARD_DATASET, b1._SCORECARD_REQUIRED, {
        "sap_b1.distributor_scorecard.closed_period": {"period": AUGUST},
        "sap_b1.distributor_scorecard.distributors": [row("empresa_b", "rojo", "rojo"), row("empresa_c", "verde")],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_distributor_scorecard(user_for(), as_of=AS_OF)
    assert (result.red, result.green, result.yellow) == (1, 1, 0)
    assert (result.sell_in_qty, result.sell_out_qty) == (24.0, 20.0)
    assert result.breaches == ["La distribuidora empresa_b esta en rojo en 2026-08: crecimiento de sell-out -8.0."]
    sql = conn.sql_for("sap_b1.distributor_scorecard.distributors")
    assert "overall_color" in sql and "LIMIT $4" in sql


@pytest.mark.asyncio
async def test_batch_expiry_reports_expired_stock_and_actions_per_item(monkeypatch):
    conn = _conn(b1.EXPIRY_DATASET, b1._EXPIRY_REQUIRED, {
        "sap_b1.batch_expiry.companies": [
            {"company": "empresa_a", "as_of": date(2026, 9, 25), "expired_qty": Decimal("5"), "expired_value": Decimal("50"),
             "horizon_value": Decimal("300"), "at_risk_value": Decimal("120"), "transfers": 1},
        ],
        "sap_b1.batch_expiry.top_items": [
            {"company": "empresa_a", "item_code": "A-1", "at_risk_qty": Decimal("7"), "at_risk_value": Decimal("70"), "transfer_candidate": "empresa_b"},
            {"company": "empresa_a", "item_code": "A-2", "at_risk_qty": Decimal("5"), "at_risk_value": Decimal("50"), "transfer_candidate": None},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_batch_expiry(user_for())
    assert result.as_of == "2026-09-25" and result.expired_value == 50.0 and result.at_risk_value == 120.0
    assert [item["action"] for item in result.top_items] == ["traspasar a empresa_b", "promocion o devolucion"]
    assert len(result.breaches) == 3 and "ya vencidos" in result.breaches[0]
    assert "within_horizon AND bucket <> 'vencido'" in conn.sql_for("sap_b1.batch_expiry.top_items")


@pytest.mark.asyncio
async def test_batch_expiry_without_stock_is_degraded(monkeypatch):
    conn = _conn(b1.EXPIRY_DATASET, b1._EXPIRY_REQUIRED, {"sap_b1.batch_expiry.companies": [], "sap_b1.batch_expiry.top_items": []})
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_batch_expiry(user_for())
    assert result.status == "degraded" and result.notes == ["sin lotes con existencia"]


@pytest.mark.asyncio
async def test_item_coverage_counts_colours_and_turns_red_items_into_orders(monkeypatch):
    conn = _conn(b1.COVERAGE_DATASET, b1._COVERAGE_REQUIRED, {
        "sap_b1.item_coverage.companies": [
            {"company": "empresa_a", "as_of": date(2026, 9, 25), "items": 40, "red": 2, "yellow": 3, "green": 33,
             "without_consumption": 2, "suggestions": 5, "suggested_value": Decimal("1500.5"),
             "median_coverage_days": Decimal("41.5"), "median_coverage_with_orders_days": Decimal("55"),
             "min_stock_outdated": 7},
            {"company": "empresa_b", "as_of": date(2026, 9, 25), "items": 10, "red": 0, "yellow": 1, "green": 9,
             "without_consumption": 0, "suggestions": 1, "suggested_value": Decimal("100"),
             "median_coverage_days": Decimal("60"), "median_coverage_with_orders_days": Decimal("60"),
             "min_stock_outdated": 0},
        ],
        "sap_b1.item_coverage.top_risks": [
            {"company": "empresa_a", "item_code": "RM-004", "coverage_color": "rojo", "coverage_days": 1.5,
             "coverage_with_orders_days": 3.0, "lead_time_days": 14, "stockout_date": date(2026, 9, 28),
             "suggested_qty": 300.0, "suggested_action": "comprar", "order_by_date": date(2026, 9, 25),
             "suggested_value": 900.0},
            {"company": "empresa_b", "item_code": "FG-002", "coverage_color": "amarillo", "coverage_days": 8.0,
             "coverage_with_orders_days": 8.0, "lead_time_days": 5, "stockout_date": date(2026, 10, 3),
             "suggested_qty": 50.0, "suggested_action": "comprar", "order_by_date": date(2026, 9, 28),
             "suggested_value": 100.0},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_item_coverage(user_for())
    assert result.as_of == "2026-09-25" and (result.red, result.yellow, result.green) == (2, 4, 42)
    assert result.suggestions == 6 and result.suggested_value == 1600.5 and result.min_stock_outdated == 7
    assert [risk["order_by"] for risk in result.top_risks] == ["2026-09-25", "2026-09-28"]
    assert result.breaches == [
        "empresa_a: 2 articulos se agotan antes de que pueda llegar un pedido nuevo, aun contando las ordenes abiertas.",
        "empresa_a: el articulo RM-004 se agota el 2026-09-28 y el tiempo de entrega es de 14 dias; comprar 300 hoy.",
    ]
    companies_sql = conn.sql_for("sap_b1.item_coverage.companies")
    assert "percentile_cont(0.5)" in companies_sql and "LIMIT $4" in companies_sql
    assert "coverage_color IN ('rojo', 'amarillo')" in conn.sql_for("sap_b1.item_coverage.top_risks")


@pytest.mark.asyncio
async def test_item_coverage_without_stock_is_degraded(monkeypatch):
    conn = _conn(b1.COVERAGE_DATASET, b1._COVERAGE_REQUIRED,
                 {"sap_b1.item_coverage.companies": [], "sap_b1.item_coverage.top_risks": []})
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_item_coverage(user_for())
    assert result.status == "degraded" and result.notes == ["sin existencias publicadas"]

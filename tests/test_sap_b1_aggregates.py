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


def _dataset(name: str, columns) -> FakeGoldDataset:
    return FakeGoldDataset(name, sorted(columns), published_at=PUBLISHED_AT)


def _conn(name: str, columns, answers: dict, extra: dict | None = None) -> FakeConn:
    datasets = {name: _dataset(name, columns)}
    for other, other_columns in (extra or {}).items():
        datasets[other] = _dataset(other, other_columns)
    return FakeConn(datasets=datasets, answers=answers)


def test_module_follows_the_aggregate_contract():
    source = (REPO_ROOT / "console/app/services/intelligence/sap_b1_aggregates.py").read_text(encoding="utf-8")
    assert "fetchall" not in source and ".format(" not in source and "SELECT *" not in source
    assert not re.search(r"\bLIMIT\s+\d", source)
    for match in re.finditer(r"\bLIMIT\s+(\S+)", source):
        assert match.group(1).startswith("$")
    assert 'f"gold_' not in source and "run_gold_aggregate(" in source and "GOLD_SCOPE_PREDICATE" in source
    notes = [value for name, value in vars(b1).items() if name.endswith("_NOTE")]
    assert len(notes) == 18
    for note in notes:
        assert "NO " in note and len(note) > 60, note


@pytest.mark.parametrize(
    "dataset, columns",
    [
        (b1.KPIS_DATASET, b1._KPI_REQUIRED),
        (b1.KPI_RECONCILIATION_DATASET, b1._KPI_RECON_REQUIRED),
        (b1.LEDGER_DATASET, b1._LEDGER_REQUIRED),
        (b1.DATA_QUALITY_DATASET, b1._DATA_QUALITY_REQUIRED),
        (b1.ENTITY_MODEL_DATASET, b1._ENTITY_REQUIRED),
        (b1.SCORECARD_DATASET, b1._SCORECARD_REQUIRED),
        (b1.SELLOUT_CLINIC_DATASET, b1._CLINIC_REQUIRED),
        (b1.EXPIRY_DATASET, b1._EXPIRY_REQUIRED),
        (b1.COVERAGE_DATASET, b1._COVERAGE_REQUIRED),
        (b1.COST_VARIANCE_DATASET, b1._COST_REQUIRED),
        (b1.LEAD_TIME_DATASET, b1._LEAD_REQUIRED),
    ],
)
def test_required_columns_are_real_outputs_of_the_gold_sql(dataset, columns):
    sql = (REPO_ROOT / "cartridges" / "sap_b1" / "datasets" / f"{dataset}.sql").read_text(encoding="utf-8")
    assert "(gold)" in sql.splitlines()[0]
    for column in columns:
        assert re.search(rf"\b{column}\b", sql), (dataset, column)


def _totals(company: str, month: date, value: str, pct: str) -> dict:
    return {"company": company, "doc_month": month, "local_currency": "MXN", "value": Decimal(value),
            "pct": Decimal(pct), "revenue": Decimal("1000"), "commission": Decimal("10")}


@pytest.mark.asyncio
async def test_margen_bruto_reports_group_and_companies_and_flags_a_group_drop(monkeypatch):
    rows = [_totals("grupo", AUGUST, "300", "30"), _totals("empresa_a", AUGUST, "200", "20")]
    rows += [_totals("grupo", date(2026, m, 1), "390", "39") for m in (7, 6, 5)]
    conn = _conn(b1.KPIS_DATASET, b1._KPI_REQUIRED, {
        "sap_b1.margen_bruto.closed_period": {"period": AUGUST},
        "sap_b1.margen_bruto.totals": rows,
    })
    install_gold_connect(monkeypatch, conn)

    result = await b1.query_margen_bruto(user_for(), as_of=AS_OF)

    assert result.status == "ready" and result.period == "2026-08" and result.currency == "MXN"
    assert result.group == {"value": 300.0, "pct": 30.0, "revenue": 1000.0, "commission": 10.0}
    assert [c["company"] for c in result.companies] == ["empresa_a"] and result.trailing_group_pct == 39.0
    assert len(result.breaches) == 1 and "cayó 9.0 puntos" in result.breaches[0]
    args = conn.args_for("sap_b1.margen_bruto.totals")
    assert args[:4] == (WORKSPACE_A, TENANT_A, "margen_bruto", AUGUST) and args[4] == date(2026, 5, 1)
    assert "indicator = 'margen_bruto'" in conn.sql_for("sap_b1.margen_bruto.closed_period")
    assert conn.scope == (TENANT_A, WORKSPACE_A) and conn.closed


@pytest.mark.asyncio
async def test_margin_without_closed_months_is_degraded_and_a_missing_column_unavailable(monkeypatch):
    install_gold_connect(monkeypatch, _conn(b1.KPIS_DATASET, b1._KPI_REQUIRED, {
        "sap_b1.margen_contribucion.closed_period": {"period": None}}))
    result = await b1.query_margen_contribucion(user_for(), as_of=AS_OF)
    assert result.status == "degraded" and result.notes == ["sin meses cerrados con datos"]
    install_gold_connect(monkeypatch, _conn(b1.KPIS_DATASET, b1._KPI_REQUIRED - {"value_pct"}, {}))
    result = await b1.query_margen_contribucion(user_for(), as_of=AS_OF)
    assert result.status == "unavailable" and result.error.startswith("invalid_schema")
    install_gold_connect(monkeypatch, FakeConn(datasets={}, answers={}))
    assert (await b1.query_destructores(user_for(), as_of=AS_OF)).status == "unavailable"


@pytest.mark.asyncio
async def test_destroyers_name_customers_only_when_asked(monkeypatch):
    answers = {
        "sap_b1.destructores.closed_period": {"period": AUGUST},
        "sap_b1.destructores.companies": [
            {"company": "empresa_b", "customers": 3, "lost": Decimal("1500.5"), "min_margin_pct": Decimal("30")},
        ],
        "sap_b1.destructores.top": [
            {"company": "empresa_b", "dim_label": "Clinica Uno", "lost": Decimal("900"), "pct": Decimal("12.5"),
             "min_margin_pct": Decimal("30"), "revenue": Decimal("5000")},
        ],
    }
    conn = _conn(b1.KPIS_DATASET, b1._KPI_REQUIRED, answers)
    install_gold_connect(monkeypatch, conn)
    anonymous = await b1.query_destructores(user_for(), as_of=AS_OF)
    assert anonymous.customers == 3 and anonymous.margin_lost == 1500.5 and anonymous.top == []
    assert anonymous.breaches == ["empresa_b: 3 clientes destruyen margen en 2026-08; margen perdido 1,500.50 contra el mínimo de 30.0 %."]
    assert "Clinica" not in " ".join(anonymous.breaches)
    install_gold_connect(monkeypatch, conn)
    named = await b1.query_destructores(user_for(), as_of=AS_OF, top_n=5)
    assert named.top == [{"company": "empresa_b", "customer": "Clinica Uno", "margin_lost": 900.0, "margin_pct": 12.5,
                          "min_margin_pct": 30.0, "revenue": 5000.0}]


@pytest.mark.asyncio
async def test_finance_matrix_counts_statuses_and_explains_outliers(monkeypatch):
    conn = _conn(b1.KPI_RECONCILIATION_DATASET, b1._KPI_RECON_REQUIRED, {
        "sap_b1.reconciliacion_finanzas.totals": {"rows": 10, "within": 8, "outside": 1, "missing": 1,
                                                  "platform_only": 2, "periods": ["2026-07", "2026-08"]},
        "sap_b1.reconciliacion_finanzas.outliers": [
            {"company": "empresa_a", "period": "2026-08", "indicator": "margen_bruto", "dimension": "cliente",
             "dim_key": "C-1", "dim_label": "Cliente", "unit": "monto", "finance_value": Decimal("100"),
             "platform_value": Decimal("98"), "delta_pct": Decimal("-2"), "venta_bruta": Decimal("500"),
             "devoluciones_nc": Decimal("10"), "descuentos": Decimal("5"), "costo": Decimal("300"), "comision": Decimal("4")},
            {"company": "empresa_a", "period": "2026-08", "indicator": "margen_bruto", "dimension": "cliente",
             "dim_key": "X", "dim_label": None, "unit": "monto", "finance_value": Decimal("1"),
             "platform_value": None, "delta_pct": None, "venta_bruta": None, "devoluciones_nc": None,
             "descuentos": None, "costo": None, "comision": None},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_reconciliacion_finanzas(user_for(), as_of=AS_OF)
    assert (result.rows, result.within, result.within_pct, result.period) == (10, 8, 80.0, "2026-08")
    assert result.outliers[0]["descuentos_pie_factura"] == 5.0 and result.outliers[0]["costo_aplicado"] == 300.0
    assert result.breaches[0] == "empresa_a 2026-08: margen bruto cliente C-1 difiere -2.0 % contra Finanzas."
    assert result.breaches[1] == "empresa_a 2026-08: margen bruto cliente X de Finanzas no existe en la plataforma."
    assert result.ledger_months == 0, "the ledger dataset is absent in this fake"


@pytest.mark.asyncio
async def test_finance_matrix_without_a_run_is_degraded_with_the_reason(monkeypatch):
    install_gold_connect(monkeypatch, _conn(b1.KPI_RECONCILIATION_DATASET, b1._KPI_RECON_REQUIRED, {
        "sap_b1.reconciliacion_finanzas.totals": {"rows": 0, "within": 0, "outside": 0, "missing": 0,
                                                  "platform_only": 0, "periods": None},
        "sap_b1.reconciliacion_finanzas.outliers": [],
    }))
    result = await b1.query_reconciliacion_finanzas(user_for(), as_of=AS_OF)
    assert result.status == "degraded" and result.notes == ["Finanzas todavía no carga su corrida manual"]
    assert result.within_pct is None and not result.breaches


@pytest.mark.asyncio
async def test_expiry_levels_priorities_and_options(monkeypatch):
    conn = _conn(b1.EXPIRY_DATASET, b1._EXPIRY_REQUIRED, {
        "sap_b1.caducidad_lotes.levels": [
            {"alert_level": "rojo", "batches": 2, "value": Decimal("800"), "at_risk": Decimal("600"), "as_of": date(2026, 9, 24)},
            {"alert_level": "vencido", "batches": 1, "value": Decimal("50"), "at_risk": Decimal("50"), "as_of": date(2026, 9, 24)},
        ],
        "sap_b1.caducidad_lotes.options": [{"action_option": "traslado_filial", "batches": 1}],
        "sap_b1.caducidad_lotes.priorities": [
            {"company": "dist_a", "item_code": "FG-1", "batch": "L1", "branch_name": "Principal", "alert_level": "rojo",
             "days": 12, "at_risk_qty": Decimal("40"), "at_risk_value": Decimal("600"), "action_option": "traslado_filial",
             "transfer_branch_name": "Filial Norte", "transfer_candidate": None, "rank": 1},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_caducidad_lotes(user_for())
    assert result.as_of == "2026-09-24" and result.at_risk_value == 600.0 and result.options == {"traslado_filial": 1}
    assert result.priorities[0]["action"] == "trasladar a la filial Filial Norte"
    assert result.breaches[0].startswith("Hay 1 lotes ya vencidos")
    assert "caduca en 12 días" in result.breaches[1]


@pytest.mark.asyncio
async def test_coverage_leads_with_plan_changes_and_offers_options(monkeypatch):
    conn = _conn(b1.COVERAGE_DATASET, b1._COVERAGE_REQUIRED, {
        "sap_b1.dias_cobertura.totals": {"as_of": date(2026, 9, 24), "red": 2, "yellow": 1, "green": 5, "idle": 1,
                                        "stockout": 1, "critical": 1, "suggestions": 2, "suggested_value": Decimal("1234")},
        "sap_b1.dias_cobertura.risks": [
            {"company": "mfg", "item_code": "RM-1", "item_name": "Materia 1", "coverage_color": "rojo", "stockout_risk": True,
             "coverage_days": Decimal("6.5"), "lead_time_days": 14, "stockout_date": date(2026, 9, 30),
             "suggested_qty": Decimal("500"), "suggested_action": "comprar", "suggested_supplier": "S-1",
             "alternate_supplier": "S-2", "order_by_date": date(2026, 9, 24), "consumption_basis": "plan",
             "is_critical": True, "criticality_rank": 1, "plan_changed": True},
        ],
        "sap_b1.dias_cobertura.plan_changes": 3,
    })
    install_gold_connect(monkeypatch, conn)
    result = await b1.query_dias_cobertura(user_for())
    assert result.colors == {"rojo": 2, "amarillo": 1, "verde": 5, "sin_consumo": 1} and result.plan_changes == 3
    risk = result.risks[0]
    assert risk["options"] == ["adelantar la orden de compra", "proveedor alterno S-2"] and risk["basis"] == "plan"
    assert result.breaches[0].startswith("El plan de producción cambió: mfg: RM-1 alcanza 6.5 días")
    assert conn.args_for("sap_b1.dias_cobertura.risks")[3] == date(2026, 9, 23)


@pytest.mark.asyncio
async def test_cost_variance_and_late_suppliers(monkeypatch):
    conn = _conn(b1.COST_VARIANCE_DATASET, b1._COST_REQUIRED, {
        "sap_b1.costo_real_vs_estandar.closed_period": {"period": AUGUST},
        "sap_b1.costo_real_vs_estandar.totals": {"items": 10, "above": 1, "variance": Decimal("80")},
        "sap_b1.costo_real_vs_estandar.worst": [
            {"company": "mfg", "item_code": "RM-2", "item_name": "Materia 2", "pct": Decimal("8"), "value": Decimal("80"),
             "max_pct": Decimal("5"), "is_raw_material": True},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    cost = await b1.query_costo_real_vs_estandar(user_for(), as_of=AS_OF)
    assert cost.above_threshold == 1 and cost.breaches == [
        "mfg: RM-2 se compró 8.0 % contra su costo en 2026-08 (máximo 5.0 %), 80.00 de diferencia."]
    conn = _conn(b1.LEAD_TIME_DATASET, b1._LEAD_REQUIRED, {
        "sap_b1.lead_time_proveedores.suppliers": [
            {"company": "mfg", "card_code": "S-3", "supplier_name": "Proveedor 3", "intercompany": False, "receipts": 4,
             "late": 4, "max_delay": 2, "lead_days": Decimal("2"), "promised_days": Decimal("1")},
            {"company": "mfg", "card_code": "S-1", "supplier_name": "Proveedor 1", "intercompany": False, "receipts": 5,
             "late": 0, "max_delay": 0, "lead_days": Decimal("2"), "promised_days": Decimal("30")},
        ],
    })
    install_gold_connect(monkeypatch, conn)
    lead = await b1.query_lead_time_proveedores(user_for(), as_of=AS_OF)
    assert lead.suppliers == 2 and [s["supplier"] for s in lead.late_suppliers] == ["Proveedor 3"]
    assert lead.late_suppliers[0]["on_time_pct"] == 0.0 and lead.period == "2026-06 a 2026-08"
    assert conn.args_for("sap_b1.lead_time_proveedores.suppliers")[2:4] == (date(2026, 6, 1), date(2026, 9, 1))

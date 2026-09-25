"""The purchasing case (coverage, open orders, reorder suggestions) reconciles with the Business One fake."""
from __future__ import annotations

import importlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fake_world import Bronze, _extract, _materialise, _rows

pytestmark = pytest.mark.usefixtures("fake_postgres")

b1 = importlib.import_module("sap_b1_fake.schema")
generator = importlib.import_module("sap_b1_fake.generator")

CONSUMPTION_TYPES = {13, 14, 15, 16, 60}
SAFETY_DAYS = 7
REVIEW_DAYS = 14
DEFAULT_LEAD = 9
COLUMNS = (
    "company", "item_code", "as_of_date", "available", "open_po_qty", "open_production_qty", "position_qty",
    "b1_on_order", "consumed_90d", "daily_consumption", "coverage_days", "coverage_with_orders_days",
    "lead_time_days", "lead_time_source", "reorder_point", "order_up_to", "b1_min_stock", "b1_max_stock",
    "coverage_color", "stockout_risk", "suggested_qty", "suggested_action", "suggested_supplier",
    "order_by_date", "min_order_qty", "order_multiple", "stockout_date",
)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


@pytest.fixture(scope="module")
def coverage(tmp_path_factory, fake_postgres, dataset):
    monkeypatch = pytest.MonkeyPatch()
    for name, value in (
        ("SAP_B1_DIALECT", "postgres"), ("SAP_B1_HOST", fake_postgres["host"]), ("SAP_B1_PORT", str(fake_postgres["port"])),
        ("SAP_B1_USER", fake_postgres["user"]), ("SAP_B1_PASSWORD", fake_postgres["password"]),
        ("SAP_B1_DATABASE", fake_postgres["database"]), ("SAP_B1_COMPANIES", fake_postgres["companies"]),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", ",".join(
        [f"mx_mfg:{code}={alias}" for alias, code in sorted(generator.INTERCOMPANY_CUSTOMER.items())]
        + [f"{alias}:{generator.INTERCOMPANY_SUPPLIER}=mx_mfg" for alias in sorted(generator.INTERCOMPANY_CUSTOMER)]
    ))
    monkeypatch.setenv("SAP_B1_BUSINESS_PARAMETERS", ";".join([
        f"setting:*:*:default_lead_time_days={DEFAULT_LEAD}", "setting:mx_mfg:*:default_lead_time_days=12",
        f"setting:*:*:safety_days={SAFETY_DAYS}", f"setting:*:*:review_period_days={REVIEW_DAYS}",
    ]))
    root = tmp_path_factory.mktemp("purchasing-bronze")
    bronze = Bronze(root, today=dataset.as_of)
    bronze.install(monkeypatch)
    _extract()
    assert not bronze.failed
    con = _materialise(root)
    try:
        yield {tuple(r[:2]): dict(zip(COLUMNS, r)) for r in _rows(con, f"SELECT {', '.join(COLUMNS)} FROM sap_b1_item_coverage")}
    finally:
        con.close()
        monkeypatch.undo()


def _truth(dataset, company: str, as_of: date) -> dict[str, dict]:
    tables = dataset.tables[company]
    out: dict[str, dict] = defaultdict(lambda: defaultdict(Decimal))
    for row in tables["OITW"]:
        item = out[row[_col("OITW", "ItemCode")]]
        item["available"] += row[_col("OITW", "OnHand")] - row[_col("OITW", "IsCommited")]
        item["b1_on_order"] += row[_col("OITW", "OnOrder")]
        item["b1_min_stock"] += row[_col("OITW", "MinStock")]
    orders = {r[_col("OPOR", "DocEntry")]: r for r in tables["OPOR"]}
    for line in tables["POR1"]:
        head = orders[line[_col("POR1", "DocEntry")]]
        if head[_col("OPOR", "CANCELED")] == "N" and line[_col("POR1", "LineStatus")] == "O":
            out[line[_col("POR1", "ItemCode")]]["open_po_qty"] += line[_col("POR1", "OpenQty")]
    for order in tables["OWOR"]:
        if order[_col("OWOR", "Status")] in ("P", "R"):
            left = order[_col("OWOR", "PlannedQty")] - order[_col("OWOR", "CmpltQty")] - order[_col("OWOR", "RjctQty")]
            out[order[_col("OWOR", "ItemCode")]]["open_production_qty"] += max(left, Decimal("0"))
    for move in tables["OINM"]:
        day = _day(move[_col("OINM", "DocDate")])
        if move[_col("OINM", "TransType")] in CONSUMPTION_TYPES and as_of - timedelta(days=90) < day <= as_of:
            out[move[_col("OINM", "ItemCode")]]["consumed"] += move[_col("OINM", "OutQty")] - move[_col("OINM", "InQty")]
    for item in tables["OITM"]:
        entry = out.get(item[_col("OITM", "ItemCode")])
        if entry is not None:
            entry["lead"] = item[_col("OITM", "LeadTime")]
            entry["min_order"] = item[_col("OITM", "MinOrdrQty")]
            entry["multiple"] = item[_col("OITM", "OrdrMulti")]
            entry["method"] = item[_col("OITM", "PrcrmntMtd")]
            entry["supplier"] = item[_col("OITM", "CardCode")]
    return out


def test_positions_and_consumption_match_the_business_one_tables(coverage, dataset):
    as_of = {row["as_of_date"] for row in coverage.values()}
    assert len(as_of) == 1
    as_of = as_of.pop()
    open_orders = 0
    for company in dataset.tables:
        truth = _truth(dataset, company, as_of)
        rows = {item: row for (c, item), row in coverage.items() if c == company}
        assert set(rows) == set(truth), company
        for item, row in rows.items():
            t = truth[item]
            assert _dec(row["available"]) == t["available"], (company, item)
            assert _dec(row["open_po_qty"]) == t["open_po_qty"], (company, item)
            assert _dec(row["open_production_qty"]) == t["open_production_qty"], (company, item)
            assert _dec(row["b1_on_order"]) == t["open_po_qty"] + t["open_production_qty"], (company, item)
            assert _dec(row["position_qty"]) == t["available"] + t["open_po_qty"] + t["open_production_qty"]
            assert _dec(row["consumed_90d"]) == max(t["consumed"], Decimal("0")), (company, item)
            assert _dec(row["b1_min_stock"]) == t["b1_min_stock"]
            expected_lead = t["lead"] if t["lead"] is not None else (12 if company == "mx_mfg" else DEFAULT_LEAD)
            assert row["lead_time_days"] == expected_lead and row["lead_time_source"] == ("articulo" if t["lead"] is not None else "parametro")
            open_orders += t["open_po_qty"] > 0
    assert open_orders >= 20, "the fake must leave purchase orders open"
    cancelled = [r for r in dataset.tables["mx_mfg"]["OPOR"] if r[_col("OPOR", "CANCELED")] == "Y"]
    assert cancelled, "a cancelled purchase order must exist and be ignored"


def test_colours_and_suggestions_follow_the_replenishment_rules(coverage, dataset):
    colours = defaultdict(int)
    suggested = 0
    for (company, item), row in coverage.items():
        daily = _dec(row["daily_consumption"])
        colours[row["coverage_color"]] += 1
        if daily == 0:
            assert row["coverage_color"] == "sin_consumo" and _dec(row["suggested_qty"]) == 0 and row["coverage_days"] is None
            continue
        exact_daily = _dec(row["consumed_90d"]) / 90
        lead = row["lead_time_days"]
        position = _dec(row["position_qty"])
        reorder = exact_daily * (lead + SAFETY_DAYS)
        up_to = exact_daily * (lead + SAFETY_DAYS + REVIEW_DAYS)
        assert abs(_dec(row["reorder_point"]) - reorder) <= Decimal("0.00001")
        assert abs(_dec(row["order_up_to"]) - up_to) <= Decimal("0.00001")
        cover = position / exact_daily
        assert abs(_dec(row["coverage_with_orders_days"]) - cover) <= Decimal("0.051")
        whole = abs(cover - cover.to_integral_value()) < Decimal("0.0001")
        if abs(cover - lead) > Decimal("0.01") and abs(position - reorder) > Decimal("0.01"):
            expected = "rojo" if cover < lead else "amarillo" if position < reorder else "verde"
            assert row["coverage_color"] == expected, (company, item)
        assert row["stockout_risk"] == (row["coverage_color"] == "rojo")
        if not whole:
            assert row["stockout_date"] == row["as_of_date"] + timedelta(days=math.floor(cover))
        if row["coverage_color"] in ("rojo", "amarillo"):
            suggested += 1
            multiple = _dec(row["order_multiple"])
            need = max(up_to - position, _dec(row["min_order_qty"]))
            qty = (need / multiple).to_integral_value(rounding="ROUND_CEILING") * multiple
            assert _dec(row["suggested_qty"]) == qty, (company, item)
            assert _dec(row["suggested_qty"]) % multiple == 0 and _dec(row["suggested_qty"]) >= _dec(row["min_order_qty"])
            if not whole:
                assert row["order_by_date"] == row["as_of_date"] + timedelta(days=max(0, math.floor(cover - lead)))
            made = company == "mx_mfg" and item.startswith("FG-")
            assert row["suggested_action"] == ("producir" if made else "comprar")
            assert (row["suggested_supplier"] is None) == made
        else:
            assert _dec(row["suggested_qty"]) == 0 and row["suggested_action"] is None and row["order_by_date"] is None
    assert suggested > 0 and colours["verde"] > 0, dict(colours)

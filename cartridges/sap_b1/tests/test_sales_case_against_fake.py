"""The sales case (sell-in, sell-out, distributor scorecard, batch expiry) reconciles with the Business One fake."""
from __future__ import annotations

import importlib
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import pytest
from fake_world import Bronze, _extract, _materialise, _rows

pytestmark = pytest.mark.usefixtures("fake_postgres")

b1 = importlib.import_module("sap_b1_fake.schema")
generator = importlib.import_module("sap_b1_fake.generator")

DISTRIBUTORS = sorted(generator.INTERCOMPANY_CUSTOMER)
THRESHOLDS = {
    "sellout_growth_min_pct": Decimal("0"),
    "sell_through_min_pct": Decimal("20"),
    "channel_days_max": Decimal("400"),
    "distributor_margin_min_pct": Decimal("5"),
    "expiry_exposed_max_pct": Decimal("50"),
}
HORIZON_DAYS = 120


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


@pytest.fixture(scope="module")
def sales(tmp_path_factory, fake_postgres, dataset):
    monkeypatch = pytest.MonkeyPatch()
    for name, value in (
        ("SAP_B1_DIALECT", "postgres"), ("SAP_B1_HOST", fake_postgres["host"]), ("SAP_B1_PORT", str(fake_postgres["port"])),
        ("SAP_B1_USER", fake_postgres["user"]), ("SAP_B1_PASSWORD", fake_postgres["password"]),
        ("SAP_B1_DATABASE", fake_postgres["database"]), ("SAP_B1_COMPANIES", fake_postgres["companies"]),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", ",".join(
        [f"mx_mfg:{code}={alias}" for alias, code in sorted(generator.INTERCOMPANY_CUSTOMER.items())]
        + [f"{alias}:{generator.INTERCOMPANY_SUPPLIER}=mx_mfg" for alias in DISTRIBUTORS]
    ))
    monkeypatch.setenv("SAP_B1_BUSINESS_PARAMETERS", ";".join(
        [f"threshold:*:*:{key}={value}" for key, value in THRESHOLDS.items()]
        + ["threshold:mx_dist_b:*:distributor_margin_min_pct=99", f"setting:*:*:expiry_horizon_days={HORIZON_DAYS}"]
    ))
    root = tmp_path_factory.mktemp("sales-bronze")
    bronze = Bronze(root, today=dataset.as_of)
    bronze.install(monkeypatch)
    _extract()
    assert not bronze.failed
    con = _materialise(root)
    try:
        yield con
    finally:
        con.close()
        monkeypatch.undo()


def _intercompany_lines(dataset):
    """Manufacturer invoice lines sold to each distributor, by buyer and month (not cancelled)."""
    tables = dataset.tables["mx_mfg"]
    buyer_of = {code: alias for alias, code in generator.INTERCOMPANY_CUSTOMER.items()}
    heads = {row[_col("OINV", "DocEntry")]: row for row in tables["OINV"]}
    out = defaultdict(list)
    for line in tables["INV1"]:
        head = heads[line[_col("INV1", "DocEntry")]]
        buyer = buyer_of.get(head[_col("OINV", "CardCode")])
        if buyer and head[_col("OINV", "CANCELED")] == "N":
            out[(buyer, _day(head[_col("OINV", "DocDate")]).strftime("%Y-%m"))].append(line)
    return out


def test_sell_in_is_what_the_manufacturer_invoiced_each_distributor(sales, dataset):
    ic = _intercompany_lines(dataset)
    scorecard = {
        (r[0], r[1]): (_dec(r[2]), _dec(r[3]))
        for r in _rows(sales, "SELECT distributor, period, sell_in_amount_local, sell_in_qty FROM sap_b1_distributor_scorecard_month")
    }
    flows = {
        (r[0], r[1]): (_dec(r[2]), _dec(r[3]), r[4])
        for r in _rows(sales, "SELECT distributor, period, SUM(sell_in_amount_local), SUM(sell_in_qty), COUNT(*) "
                              "FROM sap_b1_sellin_sellout_month GROUP BY 1, 2")
    }
    assert {d for d, _ in scorecard} == set(DISTRIBUTORS)
    checked = 0
    for month, truth in dataset.truth["mx_mfg"].items():
        for buyer in DISTRIBUTORS:
            amount = truth.intercompany_sales_lc.get(buyer, Decimal("0"))
            qty = sum((line[_col("INV1", "Quantity")] for line in ic.get((buyer, month), [])), Decimal("0"))
            if (buyer, month) not in scorecard:
                assert amount == 0, (buyer, month)
                continue
            got_amount, got_qty = scorecard[(buyer, month)]
            assert abs(got_amount - amount) <= Decimal("0.01"), (buyer, month)
            assert got_qty == qty, (buyer, month)
            flow_amount, flow_qty, rows = flows[(buyer, month)]
            assert abs(flow_amount - amount) <= Decimal("0.01") * rows and flow_qty == qty, (buyer, month)
            checked += 1
    assert checked >= 40


def test_sell_out_is_the_distributor_revenue_to_external_customers(sales, dataset):
    scorecard = {
        (r[0], r[1]): _dec(r[2])
        for r in _rows(sales, "SELECT distributor, period, sell_out_revenue_local FROM sap_b1_distributor_scorecard_month")
    }
    item_lines = {
        (r[0], r[1]): (_dec(r[2]), r[3])
        for r in _rows(sales, "SELECT distributor, period, SUM(sell_out_revenue_local), COUNT(*) "
                              "FROM sap_b1_sellin_sellout_month GROUP BY 1, 2")
    }
    for buyer in DISTRIBUTORS:
        for month, truth in dataset.truth[buyer].items():
            assert truth.intercompany_sales_lc == {}, "distributors only sell to external customers"
            assert abs(scorecard[(buyer, month)] - truth.revenue_net_lc) <= Decimal("0.01"), (buyer, month)
            # Credit memos are service documents without items, so item-level sell-out is gross of them.
            got, rows = item_lines[(buyer, month)]
            assert abs(got - (truth.revenue_net_lc + truth.credit_lc)) <= Decimal("0.01") * rows, (buyer, month)


def _stock_at(dataset, company: str, month_end: date) -> dict[str, Decimal]:
    stock: dict[str, Decimal] = defaultdict(Decimal)
    for row in dataset.tables[company]["OINM"]:
        if _day(row[_col("OINM", "DocDate")]) <= month_end:
            stock[row[_col("OINM", "ItemCode")]] += row[_col("OINM", "InQty")] - row[_col("OINM", "OutQty")]
    return stock


def test_month_end_stock_back_cast_matches_the_movement_ledger(sales, dataset):
    keys = {(r[0], r[1]): r[2] for r in _rows(sales, "SELECT company, item_code, item_key FROM sap_b1_item_crosswalk")}
    rows = _rows(sales, "SELECT distributor, doc_month, item_key, stock_end_qty FROM sap_b1_sellin_sellout_month")
    months = sorted({r[1] for r in rows})
    for month in (months[0], months[len(months) // 2], months[-1]):
        next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        month_end = date.fromordinal(next_month.toordinal() - 1)
        for buyer in DISTRIBUTORS:
            truth = defaultdict(Decimal)
            for item, qty in _stock_at(dataset, buyer, month_end).items():
                truth[keys.get((buyer, item), f"CODE:{item}")] += qty
            for distributor, doc_month, item_key, stock_end in rows:
                if distributor == buyer and doc_month == month:
                    assert _dec(stock_end) == truth[item_key], (buyer, month, item_key)

    closing = {
        r[0]: _dec(r[1])
        for r in _rows(sales, "SELECT distributor, stock_end_qty FROM sap_b1_distributor_scorecard_month "
                              "QUALIFY doc_month = MAX(doc_month) OVER (PARTITION BY distributor)")
    }
    bought = defaultdict(set)
    for (buyer, _month), lines in _intercompany_lines(dataset).items():
        bought[buyer].update(line[_col("INV1", "ItemCode")] for line in lines)
    for buyer in DISTRIBUTORS:
        expected = sum((qty for (item, _whs), qty in dataset.closing_stock[buyer].items() if item in bought[buyer]), Decimal("0"))
        assert closing[buyer] == expected, buyer


def _colour(value, limit, *, higher_is_better: bool, margin: Decimal, relative: bool = False):
    if limit is None or value is None:
        return "sin_umbral"
    value, limit = _dec(value), _dec(limit)
    warn = limit * margin if relative else (limit + margin if higher_is_better else limit - margin)
    edges = (limit, warn)
    if any(abs(value - edge) <= Decimal("0.01") for edge in edges):
        return None
    if higher_is_better:
        return "rojo" if value < limit else "amarillo" if value < warn else "verde"
    return "rojo" if value > limit else "amarillo" if value > warn else "verde"


def test_scorecard_colours_follow_the_thresholds_and_the_worst_one_wins(sales, dataset):
    rows = _rows(sales, """
        SELECT distributor, period, growth_yoy_pct, sell_through_3m_pct, channel_days, margin_pct, expiry_exposed_pct,
               growth_color, sell_through_color, channel_days_color, margin_color, expiry_color, overall_color,
               growth_mom_pct, sell_out_revenue_local, stock_end_qty, sell_out_qty
        FROM sap_b1_distributor_scorecard_month ORDER BY distributor, doc_month""")
    by_key = {(r[0], r[1]): r for r in rows}
    compared = 0
    for r in rows:
        distributor = r[0]
        margin_min = Decimal("99") if distributor == "mx_dist_b" else THRESHOLDS["distributor_margin_min_pct"]
        expected = (
            _colour(r[2], THRESHOLDS["sellout_growth_min_pct"], higher_is_better=True, margin=Decimal("5")),
            _colour(r[3], THRESHOLDS["sell_through_min_pct"], higher_is_better=True, margin=Decimal("5")),
            _colour(r[4], THRESHOLDS["channel_days_max"], higher_is_better=False, margin=Decimal("0.9"), relative=True),
            _colour(r[5], margin_min, higher_is_better=True, margin=Decimal("2")),
            _colour(r[6], THRESHOLDS["expiry_exposed_max_pct"], higher_is_better=False, margin=Decimal("0.8"), relative=True),
        )
        for got, want in zip(r[7:12], expected):
            if want is not None:
                assert got == want, r
                compared += 1
        colours = set(r[7:12])
        worst = next((c for c in ("rojo", "amarillo", "verde") if c in colours), "sin_umbral")
        assert r[12] == worst, r
        if distributor == "mx_dist_b":
            assert r[10] == "rojo" and r[12] == "rojo"

        period = r[1]
        year_ago = f"{int(period[:4]) - 1}{period[4:]}"
        truth = dataset.truth[distributor]
        if year_ago in truth and truth[year_ago].revenue_net_lc:
            growth = 100 * (truth[period].revenue_net_lc - truth[year_ago].revenue_net_lc) / abs(truth[year_ago].revenue_net_lc)
            assert abs(_dec(r[2]) - growth) <= Decimal("0.01"), r
        else:
            assert r[2] is None and r[7] == "sin_umbral"
        if r[4] is not None:
            window = [by_key[(distributor, p)] for p in sorted(p for d, p in by_key if d == distributor and p <= period)][-3:]
            sold = sum((_dec(w[16]) for w in window), Decimal("0"))
            assert abs(_dec(r[4]) - _dec(r[15]) / (sold / 90)) <= Decimal("0.1"), r
    assert compared > 5 * len(DISTRIBUTORS)
    latest = [r for r in rows if r[6] is not None]
    assert {r[0] for r in latest} == set(DISTRIBUTORS) and len(latest) == len(DISTRIBUTORS)


def _fake_batches(dataset, company: str) -> list[tuple[str, str, int, Decimal, date]]:
    tables = dataset.tables[company]
    expiry = {(r[_col("OBTN", "ItemCode")], r[_col("OBTN", "SysNumber")]): _day(r[_col("OBTN", "ExpDate")]) for r in tables["OBTN"]}
    return [
        (r[_col("OBTQ", "ItemCode")], r[_col("OBTQ", "WhsCode")], r[_col("OBTQ", "SysNumber")], r[_col("OBTQ", "Quantity")],
         expiry[(r[_col("OBTQ", "ItemCode")], r[_col("OBTQ", "SysNumber")])])
        for r in tables["OBTQ"] if r[_col("OBTQ", "Quantity")] > 0
    ]


def test_batch_expiry_matches_the_batch_ledger_and_sells_first_expired_first(sales, dataset):
    rows = _rows(sales, """
        SELECT company, item_code, warehouse, sys_number, qty, exp_date, as_of_date, days_to_expiry, bucket, within_horizon,
               daily_sales_90d, at_risk_qty, item_key, transfer_candidate, local_currency
        FROM sap_b1_batch_expiry ORDER BY company, item_code, exp_date NULLS LAST, sys_number, warehouse""")
    as_of = {r[6] for r in rows}
    assert len(as_of) == 1
    as_of = as_of.pop()
    for company in dataset.tables:
        fake = _fake_batches(dataset, company)
        mine = [r for r in rows if r[0] == company]
        assert len(mine) == len(fake), company
        assert sum((_dec(r[4]) for r in mine), Decimal("0")) == sum((b[3] for b in fake), Decimal("0")), company
        expired = [r for r in mine if r[8] == "vencido"]
        assert len(expired) == sum(1 for b in fake if b[4] < as_of), company
        assert all(_dec(r[11]) == _dec(r[4]) for r in expired)
        assert all(r[9] == (r[7] <= HORIZON_DAYS) for r in mine)
        assert {r[14] for r in mine} == {"MXN"}

    groups = defaultdict(list)
    for r in rows:
        groups[(r[0], r[1])].append(r)
    exercised = 0
    for batches in groups.values():
        cumulative = Decimal("0")
        for r in batches:
            qty, daily, days = _dec(r[4]), _dec(r[10]), r[7]
            if r[8] == "vencido":
                continue
            cumulative += qty
            expected = max(Decimal("0"), min(qty, cumulative - daily * days))
            assert abs(_dec(r[11]) - expected) <= Decimal("0.000001") * (days + 1), r
            exercised += expected > 0
    assert exercised > 0

    pace = {(r[0], r[12]): _dec(r[10]) for r in rows}
    for r in rows:
        if r[13] is not None:
            assert r[13] != r[0], r
            if (r[13], r[12]) in pace:
                assert pace[(r[13], r[12])] > pace[(r[0], r[12])], r

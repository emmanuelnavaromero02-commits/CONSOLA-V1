from __future__ import annotations

import importlib
from collections import defaultdict
from decimal import Decimal

import pytest
from fake_world import Bronze, _cents, _extract, _materialise, _rows

pytestmark = pytest.mark.usefixtures("fake_postgres")

b1 = importlib.import_module("sap_b1_fake.schema")
generator = importlib.import_module("sap_b1_fake.generator")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


@pytest.fixture(scope="module")
def finance(tmp_path_factory, fake_postgres, dataset):
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
    truth = dataset.truth["mx_mfg"]
    months = sorted(truth)[:3]
    controls = []
    for index, month in enumerate(months):
        revenue = truth[month].revenue_account_lc * (Decimal("1.02") if index == 2 else 1)
        controls.append(f"control:mx_mfg:{month}:revenue_net={revenue}")
        controls.append(f"control:mx_mfg:{month}:cogs={truth[month].cogs_lc}")
    monkeypatch.setenv("SAP_B1_BUSINESS_PARAMETERS", ";".join(
        controls + ["threshold:*:*:margin_min_pct=25", "setting:mx_ghost:*:unused=1"]
    ))
    root = tmp_path_factory.mktemp("finance-bronze")
    bronze = Bronze(root, today=dataset.as_of)
    bronze.install(monkeypatch)
    _extract()
    assert not bronze.failed
    con = _materialise(root)
    try:
        yield {"con": con, "months": months}
    finally:
        con.close()
        monkeypatch.undo()


def test_customer_and_item_views_add_up_to_the_documents(finance, dataset):
    con = finance["con"]
    by_customer = {
        (r[0], r[1]): (_dec(r[2]), r[3])
        for r in _rows(con, "SELECT company, strftime(doc_month, '%Y-%m'), SUM(revenue_net_local), COUNT(*) "
                            "FROM sap_b1_margin_by_customer_month GROUP BY 1, 2")
    }
    for alias, months in dataset.truth.items():
        for month, truth in months.items():
            got, rows = by_customer.get((alias, month), (Decimal("0"), 0))
            assert abs(got - truth.revenue_net_lc) <= Decimal("0.01") * max(rows, 1), (alias, month)

    views = {}
    for view in ("customer", "item"):
        views[view] = {
            tuple(r[:3]): (_dec(r[3]), _dec(r[4]), r[5])
            for r in _rows(con, f"SELECT company, doc_month, scope, SUM(revenue_net_local), SUM(gross_profit_net_local), COUNT(*) "
                                f"FROM sap_b1_margin_by_{view}_month GROUP BY 1, 2, 3")
        }
    assert views["customer"].keys() == views["item"].keys()
    for key, (revenue, profit, rows) in views["customer"].items():
        item_revenue, item_profit, item_rows = views["item"][key]
        slack = Decimal("0.01") * (rows + item_rows)
        assert abs(revenue - item_revenue) <= slack and abs(profit - item_profit) <= slack, key


def test_margin_thresholds_apply_to_external_sales_only(finance):
    con = finance["con"]
    for view in ("customer", "item"):
        assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_margin_by_{view}_month "
                          "WHERE scope = 'intercompany' AND min_margin_pct IS NOT NULL")[0][0] == 0
        assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_margin_by_{view}_month "
                          "WHERE scope = 'external' AND min_margin_pct <> 25")[0][0] == 0
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_margin_by_customer_month WHERE scope = 'external' "
                      "AND below_min IS DISTINCT FROM (margin_pct < 25) AND margin_pct IS NOT NULL")[0][0] == 0
    below = _rows(con, "SELECT SUM(below_min_revenue_local) FROM sap_b1_margin_by_item_month WHERE scope = 'intercompany'")[0][0]
    assert _dec(below or 0) == 0


def test_customers_are_unified_by_a_valid_tax_id_only(finance):
    rows = _rows(finance["con"], "SELECT company, card_code, customer_key, match_method, companies_sharing_key "
                                 "FROM sap_b1_customer_crosswalk")
    by = {(r[0], r[1]): (r[2], r[3], r[4]) for r in rows}
    groups: dict[int, set] = defaultdict(set)
    for member, shared in generator.SHARED_CUSTOMERS.items():
        groups[shared].add(member)
    for members in groups.values():
        keys = {by[m][0] for m in members}
        assert len(keys) == 1 and next(iter(keys)).startswith("RFC:"), members
        assert all(by[m][1] == "rfc" and by[m][2] == len(members) for m in members)
    for members, method in (
        (generator.GENERIC_RFC_CUSTOMERS, "generic_rfc"),
        (generator.MISSING_RFC_CUSTOMERS, "missing_rfc"),
        (generator.INVALID_RFC_CUSTOMERS, "invalid_rfc"),
    ):
        for alias, code in members:
            assert by[(alias, code)] == (f"{alias}:{code}", method, 1)
    assert by[("mx_mfg", "C-0004")][0] != by[("mx_dist_a", "C-0004")][0], "the same code is not the same customer"
    assert not {code for _alias, code in by} & set(generator.INTERCOMPANY_CUSTOMER.values())
    assert sum(1 for key, _method, sharing in by.values() if sharing > 1) == sum(len(m) for m in groups.values())


def test_items_are_unified_by_their_barcode(finance):
    rows = _rows(finance["con"], "SELECT company, item_code, item_key, match_method, companies_sharing_key "
                                 "FROM sap_b1_item_crosswalk")
    assert rows
    for company, code, key, method, sharing in rows:
        if code.startswith("FG-"):
            assert (key, method, sharing) == (f"EAN:{generator.item_barcode(code)}", "barcode", 3)
        else:
            assert (key, method, sharing) == (f"CODE:{code}", "code", 1), company


def test_consolidated_margin_is_external_revenue_minus_the_group_cost(finance, dataset):
    ic_customers = set(generator.INTERCOMPANY_CUSTOMER.values())
    tables = dataset.tables

    def docs(alias: str, header: str, lines: str):
        heads = {row[_col(header, "DocEntry")]: row for row in tables[alias][header]}
        for line in tables[alias][lines]:
            head = heads[line[_col(lines, "DocEntry")]]
            if head[_col(header, "CANCELED")] == "N":
                yield head, line

    cost, qty = defaultdict(Decimal), defaultdict(Decimal)
    for head, line in docs("mx_mfg", "OINV", "INV1"):
        if head[_col("OINV", "CardCode")] in ic_customers:
            item = line[_col("INV1", "ItemCode")]
            cost[item] += line[_col("INV1", "StockPrice")] * line[_col("INV1", "Quantity")]
            qty[item] += line[_col("INV1", "Quantity")]
    group_cost = {item: cost[item] / qty[item] for item in cost}

    expected: dict[str, Decimal] = defaultdict(Decimal)
    for company in dataset.companies:
        for header, lines, sign in (("OINV", "INV1", 1), ("ORIN", "RIN1", -1)):
            for head, line in docs(company.alias, header, lines):
                if head[_col(header, "CardCode")] in ic_customers:
                    continue
                day = head[_col(header, "DocDate")]
                amount = line[_col(lines, "LineTotal")]
                units = line[_col(lines, "Quantity")] or 0
                item = line[_col(lines, "ItemCode")]
                if company.role == "manufacturer":
                    unit_cost = line[_col(lines, "StockPrice")] or 0
                else:
                    unit_cost = group_cost.get(item, 0)
                expected[f"{day.year:04d}-{day.month:02d}"] += sign * (amount - units * unit_cost)

    got = {r[0]: _dec(r[1]) for r in _rows(finance["con"],
           "SELECT strftime(doc_month, '%Y-%m'), consolidated_gross_profit_local FROM sap_b1_margin_consolidated_month")}
    assert got.keys() == expected.keys()
    for month, value in expected.items():
        assert abs(got[month] - value) <= Decimal("0.01"), month
    external, consolidated = _rows(finance["con"], "SELECT SUM(external_gross_profit_local), SUM(consolidated_gross_profit_local) "
                                                   "FROM sap_b1_margin_consolidated_month")[0]
    assert consolidated > external, "the group keeps the manufacturer's markup on what the distributors resell"


def test_reconciliation_explains_the_ledger_and_grades_the_finance_totals(finance, dataset):
    rows = _rows(finance["con"],
                 "SELECT company, strftime(doc_month, '%Y-%m'), revenue_residual_local, cogs_residual_local, "
                 "platform_revenue_local, platform_cogs_local, status, revenue_diff_pct, cogs_diff_pct "
                 "FROM sap_b1_margin_reconciliation_month")
    by = {(r[0], r[1]): r for r in rows}
    assert all(_dec(r[2]) == 0 and _dec(r[3]) == 0 for r in rows), "documents plus timing explain the ledger"
    for alias, months in dataset.truth.items():
        for month, truth in months.items():
            row = by[(alias, month)]
            assert _dec(row[4]) == _cents(truth.revenue_account_lc), (alias, month)
            assert _dec(row[5]) == _cents(truth.cogs_lc), (alias, month)
    first, second, perturbed = finance["months"]
    for month in (first, second):
        assert by[("mx_mfg", month)][6] == "ok" and _dec(by[("mx_mfg", month)][7]) == 0 and _dec(by[("mx_mfg", month)][8]) == 0
    assert by[("mx_mfg", perturbed)][6] == "fuera_tolerancia"
    assert abs(_dec(by[("mx_mfg", perturbed)][7]) + Decimal("1.9608")) <= Decimal("0.0001")
    graded = {("mx_mfg", m) for m in finance["months"]}
    assert all(r[6] == "sin_control" for key, r in by.items() if key not in graded)


def test_data_quality_reports_the_defects_the_fake_carries(finance):
    rows = _rows(finance["con"], "SELECT company, check_code, total, failing, status FROM sap_b1_data_quality")
    dq = {(r[0], r[1]): (r[2], r[3], r[4]) for r in rows}
    defects = generator.MISSING_RFC_CUSTOMERS | generator.INVALID_RFC_CUSTOMERS
    for company in ("mx_mfg", "mx_dist_a", "mx_dist_b"):
        failing = sum(1 for alias, _code in defects if alias == company)
        assert dq[(company, "rfc_clientes_valido")][1] == failing
        for check in ("facturas_cliente_en_maestro", "facturas_articulo_en_maestro", "asientos_cuenta_en_catalogo",
                      "relaciones_completas", "lineas_articulo_con_costo", "lotes_con_caducidad",
                      "lotes_cuadran_con_existencia"):
            total, fails, status = dq[(company, check)]
            assert total > 0 and fails == 0 and status == "ok", (company, check)
    assert dq[("mx_mfg", "intercompania_cuadra")][1:] == (0, "ok")
    assert dq[("mx_ghost", "parametros_empresa_conocida")] == (1, 1, "bajo_umbral")
    assert dq[("mx_mfg", "parametros_empresa_conocida")] == (6, 0, "ok")


def test_company_view_is_the_customer_view_rolled_up(finance):
    company = {
        tuple(r[:3]): r[3:]
        for r in _rows(finance["con"], "SELECT company, period, scope, revenue_net_local, gross_profit_net_local, "
                                       "customers, customers_below_min, customers_negative FROM sap_b1_margin_by_company_month")
    }
    rolled = {
        tuple(r[:3]): r[3:]
        for r in _rows(finance["con"], "SELECT company, period, scope, SUM(revenue_net_local), SUM(gross_profit_net_local), "
                                       "COUNT(*), COUNT(*) FILTER (WHERE below_min), COUNT(*) FILTER (WHERE negative_margin) "
                                       "FROM sap_b1_margin_by_customer_month GROUP BY 1, 2, 3")
    }
    assert company.keys() == rolled.keys()
    for key, (revenue, profit, customers, below, negative) in company.items():
        other = rolled[key]
        slack = Decimal("0.01") * customers
        assert abs(_dec(revenue) - _dec(other[0])) <= slack and abs(_dec(profit) - _dec(other[1])) <= slack, key
        assert (customers, below, negative) == tuple(other[2:]), key

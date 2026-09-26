from __future__ import annotations

import importlib
import math
from collections import defaultdict
from decimal import Decimal

import pytest
from fake_world import Bronze, _cents, _extract, _materialise, _rows

pytestmark = pytest.mark.usefixtures("fake_postgres")

b1 = importlib.import_module("sap_b1_fake.schema")
generator = importlib.import_module("sap_b1_fake.generator")

HUNDRED = Decimal("100")
DIST_B_MIN = Decimal("30")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


def _docs(dataset, alias: str, header: str, lines: str):
    tables = dataset.tables[alias]
    heads = {row[_col(header, "DocEntry")]: row for row in tables[header]}
    for line in tables[lines]:
        head = heads[line[_col(lines, "DocEntry")]]
        if head[_col(header, "CANCELED")] == "N":
            yield head, line


def _books(dataset):
    """Per company: key -> [revenue, cost, commission, invoiced_before_discount, footer_discount, credits]."""
    books: dict[tuple, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 6)
    for company in dataset.companies:
        for header, lines, sign in (("OINV", "INV1", 1), ("ORIN", "RIN1", -1)):
            for head, line in _docs(dataset, company.alias, header, lines):
                day = head[_col(header, "DocDate")]
                period = f"{day.year:04d}-{day.month:02d}"
                gross = line[_col(lines, "LineTotal")]
                net = gross * (HUNDRED - (head[_col(header, "DiscPrcnt")] or 0)) / HUNDRED
                cost = (line[_col(lines, "StockPrice")] or 0) * (line[_col(lines, "Quantity")] or 0)
                commission = net * (line[_col(lines, "Commission")] or 0) / HUNDRED
                card = head[_col(header, "CardCode")]
                slp = head[_col(header, "SlpCode")]
                external = card not in generator.INTERCOMPANY_CUSTOMER.values()
                for key in (
                    (company.alias, period, "total", ""),
                    (company.alias, period, "cliente", card),
                    (company.alias, period, "vendedor", str(slp)),
                    (company.alias, period, "externo", card) if external else None,
                ):
                    if key is None:
                        continue
                    values = books[key]
                    values[0] += sign * net
                    values[1] += sign * cost
                    values[2] += sign * commission
                    if sign > 0:
                        values[3] += gross
                        values[4] += gross - net
                    else:
                        values[5] += net
    return books


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
    monkeypatch.setenv("SAP_B1_BUSINESS_PARAMETERS", ";".join([
        "threshold:*:*:margin_min_pct=25",
        f"threshold:mx_dist_b:*:margin_min_pct={DIST_B_MIN}",
        "setting:mx_ghost:*:unused=1",
        "setting:mx_mfg:*:reconciliation_tolerance_pct=1",
    ]))
    root = tmp_path_factory.mktemp("finance-bronze")
    bronze = Bronze(root, today=dataset.as_of)
    bronze.install(monkeypatch)
    _extract()
    assert not bronze.failed

    from app.services.finance_runs import load_finance_run

    books = _books(dataset)
    months = sorted({key[1] for key in books if key[0] == "mx_mfg"})
    first, second = months[0], months[1]
    customer = sorted(key[3] for key in books if key[:3] == ("mx_mfg", first, "cliente"))[0]
    seller = next(key[3] for key in sorted(books) if key[:3] == ("mx_mfg", first, "vendedor"))

    def gross(key) -> Decimal:
        revenue, cost = books[key][:2]
        return revenue - cost

    header = "indicador,empresa,mes,dimension,clave,valor\n"
    stale = header + f"margen_bruto,mx_mfg,{first},total,,1\n"
    load_finance_run(stale, None)
    run = header + "".join([
        f"margen_bruto,mx_mfg,{first},total,,{_cents(gross(('mx_mfg', first, 'total', '')))}\n",
        f"margen_bruto,mx_mfg,{first},cliente,{customer},{_cents(gross(('mx_mfg', first, 'cliente', customer)))}\n",
        f"margen_contribucion,mx_mfg,{first},total,,{_cents(gross(('mx_mfg', first, 'total', '')) - books[('mx_mfg', first, 'total', '')][2])}\n",
        f"margen_vendedor,mx_mfg,{first},vendedor,Vendedor {seller},{_cents(gross(('mx_mfg', first, 'vendedor', seller)))}\n",
        f"margen_bruto,mx_mfg,{second},total,,{_cents(gross(('mx_mfg', second, 'total', '')) * Decimal('1.02'))}\n",
        f"margen_bruto,mx_mfg,{second},cliente,NO-EXISTE,100\n",
        f"destructores,mx_dist_b,{first},cliente,C-0001,1\n",
    ])
    load_finance_run(run, None)
    con = _materialise(root)
    try:
        yield {"con": con, "books": books, "first": first, "second": second, "customer": customer, "seller": seller}
    finally:
        con.close()
        monkeypatch.undo()


def test_margin_detail_adds_up_to_the_documents(finance, dataset):
    con, books = finance["con"], finance["books"]
    got = {
        (r[0], r[1]): tuple(_dec(v) for v in r[2:])
        for r in _rows(con, "SELECT company, period, SUM(revenue_net_local), SUM(cost_net_local), SUM(commission_local), "
                            "SUM(contribution_margin_local), SUM(footer_discount_local), SUM(invoiced_before_discount_local), "
                            "SUM(credit_memos_local), COUNT(*) FROM sap_b1_margin_detail_month GROUP BY 1, 2")
    }
    for (alias, period, dimension, _key), values in books.items():
        if dimension != "total":
            continue
        revenue, cost, commission, invoiced, discount, credits = values
        g = got[(alias, period)]
        slack = Decimal("0.01") * int(g[7])
        assert abs(g[0] - revenue) <= slack, (alias, period, "revenue")
        assert abs(g[1] - cost) <= slack, (alias, period, "cost")
        assert abs(g[2] - commission) <= slack, (alias, period, "commission")
        assert abs(g[3] - (revenue - cost - commission)) <= slack, (alias, period, "contribution")
        assert abs(g[4] - discount) <= slack and abs(g[5] - invoiced) <= slack and abs(g[6] - credits) <= slack
        assert abs(revenue - dataset.truth[alias][period].revenue_net_lc) <= Decimal("0.01"), (alias, period)
    assert sum(v[4] for k, v in books.items() if k[2] == "total") > 0, "footer discounts are exercised"
    assert sum(v[2] for k, v in books.items() if k[2] == "total") > 0, "commissions are exercised"
    channels = {r[0] for r in _rows(con, "SELECT DISTINCT channel_name FROM sap_b1_margin_detail_month")}
    assert {"Clientes", "Intercompania"} <= channels
    sellers = {r[0] for r in _rows(con, "SELECT DISTINCT slp_name FROM sap_b1_margin_detail_month")}
    assert {f"Vendedor {i}" for i in range(1, 6)} <= sellers
    intercompany_commission = _rows(con, "SELECT SUM(commission_local) FROM sap_b1_margin_detail_month WHERE scope = 'intercompany'")[0][0]
    assert _dec(intercompany_commission) == 0


def test_the_five_indicators_match_the_documents(finance):
    con, books = finance["con"], finance["books"]
    kpi = {
        (r[0], r[1], r[2], r[3], r[4]): (r[5], r[6], r[7])
        for r in _rows(con, "SELECT company, period, indicator, dimension, dim_key, value_local, value_pct, rank_in_period "
                            "FROM sap_b1_margin_kpis_month")
    }
    for (alias, period, dimension, key), (revenue, cost, commission, *_rest) in books.items():
        if dimension == "externo":
            continue
        gross = revenue - cost
        value, pct, _rank = kpi[(alias, period, "margen_bruto", dimension, key)]
        assert abs(_dec(value) - gross) <= Decimal("0.05"), (alias, period, dimension, key)
        if revenue:
            assert abs(_dec(pct) - HUNDRED * gross / revenue) <= Decimal("0.001"), (alias, period, dimension, key)
        contribution = kpi[(alias, period, "margen_contribucion", dimension, key)][0]
        assert abs(_dec(contribution) - (gross - commission)) <= Decimal("0.05")
        if dimension == "vendedor":
            assert abs(_dec(kpi[(alias, period, "margen_vendedor", "vendedor", key)][0]) - gross) <= Decimal("0.05")

    external: dict[tuple, list] = defaultdict(list)
    for (alias, period, dimension, card), (revenue, cost, *_rest) in books.items():
        if dimension == "externo":
            external[(alias, period)].append((revenue - cost, card, revenue))
    for (alias, period), customers in external.items():
        ranked = sorted(customers, key=lambda item: (-item[0], item[1]))
        top_n = max(1, math.ceil(len(ranked) * 0.2))
        total = sum(item[0] for item in ranked)
        share = HUNDRED * sum(item[0] for item in ranked[:top_n]) / total
        value, pct, rank = kpi[(alias, period, "concentracion_top20", "total", "")]
        assert rank == top_n and abs(_dec(pct) - share) <= Decimal("0.001"), (alias, period)
        listed = sorted((r, k) for (a, p, i, d, k), (_v, _p, r) in kpi.items()
                        if (a, p, i, d) == (alias, period, "concentracion_top20", "cliente"))
        assert [k for _r, k in listed] == [item[1] for item in ranked[:top_n]], (alias, period)

        minimum = DIST_B_MIN if alias == "mx_dist_b" else Decimal(25)
        lost = sorted(
            ((revenue * minimum / HUNDRED - gross, card) for gross, card, revenue in customers
             if revenue > 0 and HUNDRED * gross / revenue < minimum),
            key=lambda item: (-item[0], item[1]),
        )
        got = sorted((r, k, v) for (a, p, i, d, k), (v, _p, r) in kpi.items() if (a, p, i) == (alias, period, "destructores"))
        assert [k for _r, k, _v in got] == [card for _lost, card in lost], (alias, period)
        for (_r, _k, value), (expected, _card) in zip(got, lost):
            assert abs(_dec(value) - expected) <= Decimal("0.05")
    assert any(i == "destructores" and a == "mx_dist_b" for (a, _p, i, _d, _k) in kpi), "a 30 % minimum makes destroyers"
    assert not any(i == "destructores" and a == "mx_mfg" for (a, _p, i, _d, _k) in kpi)

    group = {r[0]: (_dec(r[1]), _dec(r[2])) for r in _rows(con,
             "SELECT k.period, k.value_local, c.consolidated_gross_profit_local FROM sap_b1_margin_kpis_month k "
             "JOIN sap_b1_margin_consolidated_month c ON strftime(c.doc_month, '%Y-%m') = k.period "
             "WHERE k.company = 'grupo' AND k.indicator = 'margen_bruto'")}
    assert group and all(value == consolidated for value, consolidated in group.values())


def test_finance_matrix_is_row_by_row_with_its_diagnostics(finance):
    con = finance["con"]
    rows = _rows(con, "SELECT company, period, indicator, dimension, dim_key, finance_value, platform_value, delta_pct, status, "
                      "venta_bruta, devoluciones_nc, descuentos_pie_factura, costo_aplicado, comision FROM sap_b1_kpi_reconciliation")
    by = {(r[0], r[1], r[2], r[3], r[4]): r[5:] for r in rows}
    first, second, customer, seller = finance["first"], finance["second"], finance["customer"], finance["seller"]
    for key in (
        ("mx_mfg", first, "margen_bruto", "total", ""),
        ("mx_mfg", first, "margen_bruto", "cliente", customer),
        ("mx_mfg", first, "margen_contribucion", "total", ""),
        ("mx_mfg", first, "margen_vendedor", "vendedor", f"Vendedor {seller}"),
    ):
        finance_value, platform_value, delta_pct, status, *diagnostics = by[key]
        assert status == "ok" and abs(_dec(delta_pct)) < Decimal("0.01"), key
        assert all(value is not None for value in diagnostics), key
    total = by[("mx_mfg", first, "margen_bruto", "total", "")]
    books = finance["books"][("mx_mfg", first, "total", "")]
    assert _dec(total[4]) == _cents(books[3]) and _dec(total[6]) == _cents(books[4]) and _dec(total[7]) == _cents(books[1])
    assert _dec(total[0]) != 1, "the newest upload of the month replaces the older one"
    off = by[("mx_mfg", second, "margen_bruto", "total", "")]
    assert off[3] == "fuera_tolerancia" and abs(_dec(off[2]) + Decimal("1.9608")) <= Decimal("0.001")
    assert by[("mx_mfg", second, "margen_bruto", "cliente", "NO-EXISTE")][3] == "sin_dato_plataforma"
    dist = {k[4]: v[3] for k, v in by.items() if k[:3] == ("mx_dist_b", first, "destructores")}
    assert "solo_plataforma" in dist.values(), "destroyers Finance did not list are shown"


def test_consolidated_margin_is_external_revenue_minus_the_group_cost(finance, dataset):
    ic_customers = set(generator.INTERCOMPANY_CUSTOMER.values())
    cost, qty = defaultdict(Decimal), defaultdict(Decimal)
    for head, line in _docs(dataset, "mx_mfg", "OINV", "INV1"):
        if head[_col("OINV", "CardCode")] in ic_customers:
            item = line[_col("INV1", "ItemCode")]
            net = line[_col("INV1", "LineTotal")] * (HUNDRED - head[_col("OINV", "DiscPrcnt")]) / HUNDRED
            cost[item] += line[_col("INV1", "StockPrice")] * line[_col("INV1", "Quantity")]
            qty[item] += line[_col("INV1", "Quantity")]
            assert net == line[_col("INV1", "LineTotal")], "intercompany invoices carry no footer discount"
    group_cost = {item: cost[item] / qty[item] for item in cost}

    expected: dict[str, Decimal] = defaultdict(Decimal)
    for company in dataset.companies:
        for header, lines, sign in (("OINV", "INV1", 1), ("ORIN", "RIN1", -1)):
            for head, line in _docs(dataset, company.alias, header, lines):
                if head[_col(header, "CardCode")] in ic_customers:
                    continue
                day = head[_col(header, "DocDate")]
                amount = line[_col(lines, "LineTotal")] * (HUNDRED - head[_col(header, "DiscPrcnt")]) / HUNDRED
                units = line[_col(lines, "Quantity")] or 0
                item = line[_col(lines, "ItemCode")]
                unit_cost = (line[_col(lines, "StockPrice")] or 0) if company.role == "manufacturer" else group_cost.get(item, 0)
                expected[f"{day.year:04d}-{day.month:02d}"] += sign * (amount - units * unit_cost)

    got = {r[0]: _dec(r[1]) for r in _rows(finance["con"],
           "SELECT strftime(doc_month, '%Y-%m'), consolidated_gross_profit_local FROM sap_b1_margin_consolidated_month")}
    assert got.keys() == expected.keys()
    for month, value in expected.items():
        assert abs(got[month] - value) <= Decimal("0.01"), month
    external, consolidated = _rows(finance["con"], "SELECT SUM(external_gross_profit_local), SUM(consolidated_gross_profit_local) "
                                                   "FROM sap_b1_margin_consolidated_month")[0]
    assert consolidated > external, "the group keeps the manufacturer's markup on what the distributors resell"


def test_documents_explain_the_ledger(finance, dataset):
    rows = _rows(finance["con"],
                 "SELECT company, strftime(doc_month, '%Y-%m'), revenue_residual_local, cogs_residual_local, "
                 "platform_revenue_local, platform_cogs_local, status FROM sap_b1_margin_reconciliation_month")
    by = {(r[0], r[1]): r for r in rows}
    assert all(_dec(r[2]) == 0 and _dec(r[3]) == 0 for r in rows), "documents plus timing explain the ledger"
    assert all(r[6] == "cuadra" for r in rows)
    for alias, months in dataset.truth.items():
        for month, truth in months.items():
            row = by[(alias, month)]
            assert _dec(row[4]) == _cents(truth.revenue_account_lc), (alias, month)
            assert _dec(row[5]) == _cents(truth.cogs_lc), (alias, month)


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
    assert dq[("mx_mfg", "parametros_empresa_conocida")] == (1, 0, "ok")


def test_entity_model_unifies_the_three_companies(finance, dataset):
    rows = _rows(finance["con"], "SELECT entity, company, records, identities, shared_identities, complete_records, orphans "
                                 "FROM sap_b1_entity_model")
    model = {(r[0], r[1]): r[2:] for r in rows}
    assert {r[0] for r in rows} == {"cliente", "proveedor", "producto", "materia_prima", "canal", "vendedor", "distribuidora", "lote"}
    incomplete = generator.MISSING_RFC_CUSTOMERS | generator.INVALID_RFC_CUSTOMERS | generator.GENERIC_RFC_CUSTOMERS
    for company in dataset.companies:
        customers = [row for row in dataset.tables[company.alias]["OCRD"]
                     if row[_col("OCRD", "CardType")] == "C" and row[_col("OCRD", "CardCode")] not in generator.INTERCOMPANY_CUSTOMER.values()]
        records, _identities, _shared, complete, orphans = model[("cliente", company.alias)]
        assert records == len(customers) and orphans == 0
        assert complete == len(customers) - sum(1 for alias, _code in incomplete if alias == company.alias)
        assert model[("vendedor", company.alias)][:4] == (5, 5, 5, 5)
        batches = len(dataset.tables[company.alias]["OBTN"])
        assert model[("lote", company.alias)][0] == batches and model[("lote", company.alias)][3] == batches
    shared_customers = len(set(generator.SHARED_CUSTOMERS.values()))
    assert model[("cliente", "grupo")][2] == shared_customers
    assert model[("producto", "grupo")][2] == len([c for c in dataset.tables["mx_mfg"]["OITM"] if c[0].startswith("FG-")])
    assert model[("materia_prima", "mx_mfg")][:4] == (15, 15, 0, 15)
    assert ("materia_prima", "mx_dist_a") not in model
    assert model[("distribuidora", "grupo")][:4] == (2, 2, 0, 2)
    assert model[("proveedor", "mx_mfg")][:4] == (10, 10, 0, 10) and ("proveedor", "mx_dist_a") not in model

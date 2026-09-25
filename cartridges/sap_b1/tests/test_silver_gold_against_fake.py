from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fake_world import Bronze, _cents, _dataset_files, _extract, _materialise, _month_map, _pg, _pg_exec, _rows

pytestmark = pytest.mark.usefixtures("fake_postgres")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _schema(dataset, alias: str) -> str:
    return next(c.schema for c in dataset.companies if c.alias == alias)


COUNT_SHORTFALL = ("mx_mfg", "OINV", 3)
COUNT_FAILED = ("mx_dist_a", "OJDT")


def _write_source_counts(dataset, dsn) -> None:
    from app.services import source_counts_mapping as scm
    from app.services.catalog_service import _yaml_entities
    from app.services.parquet_service import write_parquet_and_upload

    start = datetime.combine(dataset.start_month, datetime.min.time())
    end = datetime.combine(dataset.as_of + timedelta(days=1), datetime.min.time())
    for company in dataset.companies:
        counts = []
        catalog = {e["entity"]: e for e in _yaml_entities()}
        for entity in catalog.values():
            name = entity["entity"]
            table = f'"{company.schema}"."{name}"'
            if not entity.get("date_field"):
                sql, params = f"SELECT COUNT(*) FROM {table}", ()
            elif entity.get("parent"):
                head = catalog[entity["parent"]]
                sql = (f'SELECT COUNT(*) FROM {table} l JOIN "{company.schema}"."{head["entity"]}" h '
                       f'ON h."{entity["parent_key"]}" = l."{entity["join_key"]}" '
                       f'WHERE h."{head["date_field"]}" >= %s AND h."{head["date_field"]}" < %s')
                params = (start, end)
            else:
                sql = f'SELECT COUNT(*) FROM {table} WHERE "{entity["date_field"]}" >= %s AND "{entity["date_field"]}" < %s'
                params = (start, end)
            rows = _pg(dsn, sql, params)[0][0]
            error = None
            if (company.alias, name) == COUNT_SHORTFALL[:2]:
                rows += COUNT_SHORTFALL[2]
            if (company.alias, name) == COUNT_FAILED:
                rows, error = None, "timeout"
            counts.append(scm.SourceCount(company=company.alias, entity=name,
                                          window_start=start if entity.get("date_field") else None,
                                          window_end=end, source_rows=rows, counted_at=end, error=error))
        write_parquet_and_upload(
            entity=scm.ENTITY,
            rows=scm.records(counts),
            run_id=f"counts-{company.alias}",
            load_type="full",
            expected_columns=[*scm.COLUMNS, "_company", "_source_updated_at"],
            arrow_schema=scm.arrow_schema(),
        )


@pytest.fixture(scope="module")
def world(tmp_path_factory, fake_postgres, dataset):
    import importlib

    generator = importlib.import_module("sap_b1_fake.generator")
    b1 = importlib.import_module("sap_b1_fake.schema")
    monkeypatch = pytest.MonkeyPatch()
    for name, value in (
        ("SAP_B1_DIALECT", "postgres"), ("SAP_B1_HOST", fake_postgres["host"]), ("SAP_B1_PORT", str(fake_postgres["port"])),
        ("SAP_B1_USER", fake_postgres["user"]), ("SAP_B1_PASSWORD", fake_postgres["password"]),
        ("SAP_B1_DATABASE", fake_postgres["database"]), ("SAP_B1_COMPANIES", fake_postgres["companies"]),
    ):
        monkeypatch.setenv(name, value)
    mapping = ",".join(
        [f"mx_mfg:{code}={alias}" for alias, code in sorted(generator.INTERCOMPANY_CUSTOMER.items())]
        + [f"{alias}:{generator.INTERCOMPANY_SUPPLIER}=mx_mfg" for alias in sorted(generator.INTERCOMPANY_CUSTOMER)]
    )
    monkeypatch.setenv("SAP_B1_INTERCOMPANY", mapping)
    root = tmp_path_factory.mktemp("bronze")
    bronze = Bronze(root, today=dataset.as_of)
    bronze.install(monkeypatch)
    dsn = fake_postgres["dsn"]

    _extract()
    assert not bronze.failed
    assert bronze.watermarks, "the full load records watermarks"

    mfg = _schema(dataset, "mx_mfg")
    later = datetime.combine(dataset.as_of + timedelta(days=3), datetime.min.time()).replace(hour=10)
    edited = [r[0] for r in _pg(dsn, f'SELECT "DocEntry" FROM "{mfg}"."OINV" WHERE "CANCELED" = %s ORDER BY "DocEntry" DESC LIMIT 3', ("N",))]
    original_oinv_stamps = _pg(
        dsn,
        f'SELECT "DocEntry", "Comments", "UpdateDate", "UpdateTS" FROM "{mfg}"."OINV" WHERE "DocEntry" = ANY(%s)',
        (edited,),
    )
    _pg_exec(dsn, f'UPDATE "{mfg}"."OINV" SET "Comments" = %s, "UpdateDate" = %s, "UpdateTS" = %s WHERE "DocEntry" = ANY(%s)',
             ("editado tras la carga", later.replace(hour=0), 100000, edited))
    dropped_line = _pg(dsn, f'SELECT "LineNum", "LineTotal" FROM "{mfg}"."INV1" WHERE "DocEntry" = %s ORDER BY "LineNum" DESC LIMIT 1', (edited[0],))[0]
    inv1_columns = b1.columns("INV1")
    dropped_inv1_row = _pg(
        dsn,
        f'SELECT {", ".join(b1.quote(c) for c in inv1_columns)} FROM "{mfg}"."INV1" '
        'WHERE "DocEntry" = %s AND "LineNum" = %s',
        (edited[0], dropped_line[0]),
    )[0]
    _pg_exec(dsn, f'DELETE FROM "{mfg}"."INV1" WHERE "DocEntry" = %s AND "LineNum" = %s', (edited[0], dropped_line[0]))
    removed_stock = _pg(dsn, f'SELECT "ItemCode", "WhsCode" FROM "{mfg}"."OITW" WHERE "OnHand" = 0 ORDER BY 1, 2 LIMIT 1')[0]
    oitw_columns = b1.columns("OITW")
    removed_oitw_row = _pg(
        dsn,
        f'SELECT {", ".join(b1.quote(c) for c in oitw_columns)} FROM "{mfg}"."OITW" '
        'WHERE "ItemCode" = %s AND "WhsCode" = %s',
        removed_stock,
    )[0]
    _pg_exec(dsn, f'DELETE FROM "{mfg}"."OITW" WHERE "ItemCode" = %s AND "WhsCode" = %s', removed_stock)

    _extract(["OINV", "INV1"], mode="incremental")
    _extract(["OITW"], mode="full")
    assert not bronze.failed
    _write_source_counts(dataset, dsn)

    con = _materialise(root)
    try:
        yield {
            "con": con, "dsn": dsn, "bronze": bronze, "edited": edited, "dropped_line": dropped_line,
            "removed_stock": removed_stock, "later": later, "generator": generator,
        }
    finally:
        con.close()
        for doc_entry, comments, update_date, update_ts in original_oinv_stamps:
            _pg_exec(
                dsn,
                f'UPDATE "{mfg}"."OINV" SET "Comments" = %s, "UpdateDate" = %s, "UpdateTS" = %s WHERE "DocEntry" = %s',
                (comments, update_date, update_ts, doc_entry),
            )
        _pg_exec(
            dsn,
            f'INSERT INTO "{mfg}"."INV1" ({", ".join(b1.quote(c) for c in inv1_columns)}) '
            f'VALUES ({", ".join(["%s"] * len(inv1_columns))})',
            dropped_inv1_row,
        )
        _pg_exec(
            dsn,
            f'INSERT INTO "{mfg}"."OITW" ({", ".join(b1.quote(c) for c in oitw_columns)}) '
            f'VALUES ({", ".join(["%s"] * len(oitw_columns))})',
            removed_oitw_row,
        )
        monkeypatch.undo()


def test_every_latest_matches_the_source_after_the_edits(world, dataset):
    import importlib

    b1 = importlib.import_module("sap_b1_fake.schema")
    con, dsn = world["con"], world["dsn"]
    for table in b1.TABLES:
        for company in dataset.companies:
            expected = _pg(dsn, f'SELECT COUNT(*) FROM "{company.schema}"."{table}"')[0][0]
            got = _rows(con, f"SELECT COUNT(*) FROM sap_b1_{table.lower()}_latest WHERE company = '{company.alias}'")[0][0]
            assert got == expected, f"{company.alias}.{table}: silver has {got}, the source has {expected}"


def test_edited_documents_show_once_with_their_new_version(world):
    con = world["con"]
    for entry in world["edited"]:
        rows = _rows(con, f"SELECT comments, source_updated_at FROM sap_b1_oinv_latest WHERE company = 'mx_mfg' AND doc_entry = {entry}")
        assert rows == [("editado tras la carga", world["later"].strftime("%Y-%m-%dT%H:%M:%S"))]
    versions = _rows(con, "SELECT COUNT(*) FROM read_parquet('" + (world["bronze"].root / "raw/sap_b1/OINV").as_posix() + f"/**/*.parquet', hive_partitioning=true) WHERE _company = 'mx_mfg' AND DocEntry = {world['edited'][0]}")
    assert versions[0][0] == 2, "bronze keeps both versions; silver shows one"


def test_a_line_dropped_from_a_document_disappears_from_silver(world):
    con = world["con"]
    entry, (line_num, _total) = world["edited"][0], world["dropped_line"]
    assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_inv1_latest WHERE company = 'mx_mfg' AND doc_entry = {entry} AND line_num = {line_num}") == [(0,)]
    assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_ar_invoice_lines WHERE company = 'mx_mfg' AND doc_entry = {entry} AND line_num = {line_num}") == [(0,)]
    remaining = _rows(con, f"SELECT COUNT(*) FROM sap_b1_ar_invoice_lines WHERE company = 'mx_mfg' AND doc_entry = {entry}")[0][0]
    assert remaining == _pg(world["dsn"], 'SELECT COUNT(*) FROM "%s"."INV1" WHERE "DocEntry" = %%s' % world["generator"].DEFAULT_COMPANIES[0].schema, (entry,))[0][0] > 0


def test_a_snapshot_row_removed_from_the_source_is_not_resurrected(world):
    con = world["con"]
    item, whs = world["removed_stock"]
    assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_oitw_latest WHERE company = 'mx_mfg' AND item_code = '{item}' AND whs_code = '{whs}'") == [(0,)]
    assert _rows(con, f"SELECT COUNT(*) FROM sap_b1_stock_on_hand WHERE company = 'mx_mfg' AND item_code = '{item}' AND warehouse = '{whs}'") == [(0,)]
    loads = _rows(con, "SELECT COUNT(DISTINCT _run_id) FROM read_parquet('" + (world["bronze"].root / "raw/sap_b1/OITW").as_posix() + "/**/*.parquet', hive_partitioning=true) WHERE _company = 'mx_mfg'")
    assert loads[0][0] >= 2, "two snapshot runs exist in bronze"


def test_currency_is_never_null_on_any_amount_row(world):
    con = world["con"]
    for path in _dataset_files():
        columns = {c[0] for c in con.execute(f'DESCRIBE "{path.stem}"').fetchall()}
        for column in ("doc_currency", "local_currency", "sys_currency"):
            if column in columns:
                nulls = _rows(con, f'SELECT COUNT(*) FROM "{path.stem}" WHERE {column} IS NULL')[0][0]
                assert nulls == 0, f"{path.stem}: {nulls} rows without {column}"
    usd = _rows(con, "SELECT company, local_currency, sys_currency FROM sap_b1_company ORDER BY company")
    assert usd == [("mx_dist_a", "MXN", "USD"), ("mx_dist_b", "MXN", "MXN"), ("mx_mfg", "MXN", "MXN")]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_ar_invoice_lines WHERE doc_currency <> local_currency") == [(0,)]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_ar_invoice_lines WHERE amount_doc <> amount_local") == [(0,)]
    off = _rows(con, "SELECT COUNT(*) FROM sap_b1_ar_invoice_lines WHERE company = 'mx_dist_a' AND amount_sys = amount_local AND amount_local <> 0")
    assert off == [(0,)], "system-currency amounts follow the daily rate for the USD company"


def test_sales_gold_matches_the_invoice_lines_of_the_source(world, dataset):
    con, dsn = world["con"], world["dsn"]
    for company in dataset.companies:
        s = company.schema
        expected = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."LineTotal" * (100 - h."DiscPrcnt") / 100) FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        cost = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."StockPrice" * l."Quantity") FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        profit = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."GrssProfit" - l."LineTotal" * h."DiscPrcnt" / 100) FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        credits = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."LineTotal") FROM "{s}"."ORIN" h JOIN "{s}"."RIN1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        gold = _rows(con,
            f"SELECT doc_month, SUM(revenue_gross_local), SUM(cost_local), SUM(gross_profit_local), SUM(credit_memos_local) "
            f"FROM sap_b1_sales_by_company_month WHERE company = '{company.alias}' GROUP BY 1")
        got = {(r[0],): (Decimal(str(r[1])), Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4]))) for r in gold}
        assert set(got) == set(expected), f"{company.alias}: months differ"
        for month, revenue in expected.items():
            g_rev, g_cost, g_profit, g_credit = got[month]
            assert g_rev == _cents(revenue), f"{company.alias} {month}: revenue"
            assert g_cost == _cents(cost[month]), f"{company.alias} {month}: cost from the lines"
            assert g_profit == _cents(profit[month]), f"{company.alias} {month}: gross profit"
            assert g_credit == _cents(credits.get(month, Decimal(0))), f"{company.alias} {month}: credit memos"
        assert sum(cost.values()) > 0 and sum(profit.values()) > 0
    discounted = _pg(dsn, f'SELECT COUNT(*) FROM "{_schema(dataset, "mx_mfg")}"."OINV" WHERE "DiscPrcnt" > 0 AND "CANCELED" = %s', ("N",))[0][0]
    assert discounted > 0, "the fake must carry footer discounts so the net amounts are exercised"
    scopes = {r[0] for r in _rows(con, "SELECT DISTINCT scope FROM sap_b1_sales_by_company_month WHERE company = 'mx_mfg'")}
    assert scopes == {"external", "intercompany"}
    assert {r[0] for r in _rows(con, "SELECT DISTINCT scope FROM sap_b1_sales_by_company_month WHERE company <> 'mx_mfg'")} == {"external"}


def test_intercompany_is_eliminated_and_reconciles_on_both_sides(world, dataset):
    con, dsn, generator = world["con"], world["dsn"], world["generator"]
    recon = _rows(con, "SELECT seller, buyer, doc_month, sold_local, bought_local, difference_local, reconciled FROM sap_b1_intercompany_reconciliation_month ORDER BY 1, 2, 3")
    assert recon, "the reconciliation has rows"
    assert {(r[0], r[1]) for r in recon} == {("mx_mfg", "mx_dist_a"), ("mx_mfg", "mx_dist_b")}
    assert all(r[6] and r[5] == 0 and r[3] > 0 for r in recon), [r for r in recon if not r[6]]
    mfg = _schema(dataset, "mx_mfg")
    for alias, customer in sorted(generator.INTERCOMPANY_CUSTOMER.items()):
        sold = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."LineTotal") FROM "{mfg}"."OINV" h JOIN "{mfg}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CardCode" = %s AND h."CANCELED" = %s GROUP BY 1', (customer, "N")))
        got = {(r[0],): Decimal(str(r[1])) for r in _rows(con, f"SELECT doc_month, sold_local FROM sap_b1_intercompany_reconciliation_month WHERE buyer = '{alias}'")}
        assert got == {k: _cents(v) for k, v in sold.items()}
    consolidated = _rows(con, "SELECT doc_month, revenue_gross_local, intercompany_eliminated_local, companies FROM sap_b1_sales_consolidated_month ORDER BY 1")
    by_company = _rows(con, "SELECT doc_month, SUM(CASE WHEN scope = 'external' THEN revenue_gross_local ELSE 0 END), SUM(CASE WHEN scope = 'intercompany' THEN revenue_gross_local ELSE 0 END) FROM sap_b1_sales_by_company_month GROUP BY 1 ORDER BY 1")
    assert [r[0] for r in consolidated] == [r[0] for r in by_company]
    assert all(abs(Decimal(str(c[1])) - Decimal(str(b[1]))) <= Decimal("0.02") for c, b in zip(consolidated, by_company)), "rounding per row only"
    assert all(r[3] == 3 for r in consolidated)
    total_all = _rows(con, "SELECT SUM(revenue_gross_local) FROM sap_b1_sales_by_company_month")[0][0]
    total_consolidated = _rows(con, "SELECT SUM(revenue_gross_local) FROM sap_b1_sales_consolidated_month")[0][0]
    eliminated = _rows(con, "SELECT SUM(intercompany_eliminated_local) FROM sap_b1_sales_consolidated_month")[0][0]
    assert total_consolidated < total_all and eliminated > 0
    credits_ic = _rows(con, "SELECT SUM(amount_local) FROM sap_b1_ar_credit_memo_lines WHERE is_intercompany AND canceled = 'N'")[0][0] or 0
    assert abs(Decimal(str(total_all)) - Decimal(str(total_consolidated)) - Decimal(str(eliminated))) <= Decimal("0.01") * 48, "per-month rounding only"


def test_pnl_gold_matches_the_journal_of_the_source(world, dataset):
    con, dsn, generator = world["con"], world["dsn"], world["generator"]
    for company in dataset.companies:
        s = company.schema
        revenue = _month_map(_pg(dsn, f'SELECT DATE_TRUNC(\'month\', j."RefDate")::date, SUM(j."Credit" - j."Debit") FROM "{s}"."JDT1" j WHERE j."Account" = %s GROUP BY 1', (generator.ACCT_REVENUE,)))
        cogs = _month_map(_pg(dsn, f'SELECT DATE_TRUNC(\'month\', j."RefDate")::date, SUM(j."Debit" - j."Credit") FROM "{s}"."JDT1" j WHERE j."Account" = %s GROUP BY 1', (generator.ACCT_COGS,)))
        got = {(r[0],): (Decimal(str(r[1])), Decimal(str(r[2]))) for r in _rows(con, f"SELECT doc_month, revenue_local, expenses_local FROM sap_b1_pnl_by_company_month WHERE company = '{company.alias}'")}
        assert set(got) == set(revenue) | set(cogs)
        for month in got:
            assert got[month][0] == _cents(revenue.get(month, Decimal(0))), f"{company.alias} {month}: revenue account"
            assert got[month][1] == _cents(cogs.get(month, Decimal(0))), f"{company.alias} {month}: cost of goods"
    unbalanced = _rows(con, "SELECT COUNT(*) FROM (SELECT company, trans_id FROM sap_b1_journal_lines GROUP BY 1, 2 HAVING SUM(net_debit_local) <> 0 OR SUM(net_debit_sys) <> 0) u")
    assert unbalanced == [(0,)]
    partner_lines = _rows(con, "SELECT COUNT(*) FROM sap_b1_journal_lines WHERE partner_code IS NOT NULL AND account_type = 'N'")[0][0]
    assert partner_lines > 0
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_journal_lines WHERE is_intercompany_partner")[0][0] > 0
    usd = _rows(con, "SELECT COUNT(*) FROM sap_b1_pnl_by_company_month WHERE company = 'mx_dist_a' AND revenue_sys = revenue_local AND revenue_local <> 0")
    assert usd == [(0,)]


def test_inventory_gold_and_movements_agree_with_the_source(world, dataset):
    con, dsn = world["con"], world["dsn"]
    for company in dataset.companies:
        s = company.schema
        expected = {(r[0], r[1]): Decimal(str(r[2])) for r in _pg(dsn, f'SELECT "ItemCode", "Warehouse", SUM("InQty" - "OutQty") FROM "{s}"."OINM" GROUP BY 1, 2')}
        got = {(r[0], r[1]): Decimal(str(r[2])) for r in _rows(con, f"SELECT item_code, warehouse, SUM(net_qty) FROM sap_b1_inventory_movements WHERE company = '{company.alias}' GROUP BY 1, 2")}
        assert got == expected, f"{company.alias}: movements"
        on_hand = {(r[0], r[1]): Decimal(str(r[2])) for r in _pg(dsn, f'SELECT "ItemCode", "WhsCode", "OnHand" FROM "{s}"."OITW"')}
        silver = {(r[0], r[1]): Decimal(str(r[2])) for r in _rows(con, f"SELECT item_code, warehouse, on_hand FROM sap_b1_stock_on_hand WHERE company = '{company.alias}'")}
        assert silver == on_hand, f"{company.alias}: stock snapshot"
        per_whs = {r[0]: Decimal(str(r[1])) for r in _rows(con, f"SELECT warehouse, on_hand_qty FROM sap_b1_stock_by_company_warehouse WHERE company = '{company.alias}'")}
        for whs, qty in per_whs.items():
            assert qty == sum((v for (i, w), v in on_hand.items() if w == whs), Decimal(0))
    transfers = _rows(con, "SELECT company, COUNT(*), SUM(quantity) FROM sap_b1_transfer_lines GROUP BY 1")
    assert [r[0] for r in transfers] == ["mx_mfg"] and transfers[0][1] > 0
    parked = _rows(con, "SELECT item_code, SUM(net_qty) FROM sap_b1_inventory_movements WHERE company = 'mx_mfg' AND warehouse = '02' GROUP BY 1")
    assert parked and all(r[1] == 0 for r in parked)
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_transfer_lines WHERE local_currency IS NULL") == [(0,)]


def test_masters_carry_group_names_and_intercompany_flags(world, dataset):
    con = world["con"]
    partners = _rows(con, "SELECT company, card_code, card_type, counterparty_company FROM sap_b1_business_partners WHERE is_intercompany ORDER BY 1, 2")
    assert partners == [
        ("mx_dist_a", "V-IC-MFG", "S", "mx_mfg"),
        ("mx_dist_b", "V-IC-MFG", "S", "mx_mfg"),
        ("mx_mfg", "C-IC-DIST-A", "C", "mx_dist_a"),
        ("mx_mfg", "C-IC-DIST-B", "C", "mx_dist_b"),
    ]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_business_partners WHERE group_name IS NULL") == [(0,)]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_items WHERE item_group_name IS NULL") == [(0,)]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_items WHERE batch_managed") [0][0] > 0
    orders = _rows(con, "SELECT COUNT(DISTINCT doc_entry), COUNT(*) FROM sap_b1_production_orders WHERE company = 'mx_mfg'")[0]
    assert orders[0] > 0 and orders[1] > orders[0]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_purchases_by_company_month WHERE scope = 'intercompany' AND company = 'mx_mfg'") == [(0,)]
    assert _rows(con, "SELECT COUNT(*) FROM sap_b1_purchases_by_company_month WHERE scope = 'intercompany' AND company <> 'mx_mfg'")[0][0] > 0


def test_load_reconciliation_compares_source_counts_with_silver(world, dataset):
    rows = _rows(world["con"], "SELECT company, entity, business_name, source_rows, platform_rows, difference, loaded_pct, status "
                               "FROM sap_b1_load_reconciliation")
    by = {(r[0], r[1]): r[2:] for r in rows}
    entities = {r[1] for r in rows}
    assert len(entities) == 48 and all(r[2] for r in rows), "every table carries its business name"
    shortfall = by[COUNT_SHORTFALL[:2]]
    assert shortfall[-1] == "faltan" and shortfall[3] == -COUNT_SHORTFALL[2]
    assert by[COUNT_FAILED][-1] == "sin_conteo"
    others = [r for key, r in by.items() if key not in (COUNT_SHORTFALL[:2], COUNT_FAILED)]
    assert all(r[-1] == "ok" and r[3] == 0 and _dec(r[4]) == 100 for r in others), [r for r in others if r[-1] != "ok"]

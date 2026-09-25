from __future__ import annotations

import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("fake_postgres")

CARTRIDGE = Path(__file__).resolve().parents[1]
DATASETS = CARTRIDGE / "datasets"
HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_b1\s*$")


class Bronze:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.watermarks: dict[str, str] = {}
        self.failed: list[dict] = []
        self.runs = 0

    def install(self, monkeypatch) -> None:
        from app.core import b1_source
        from app.services import extraction_service as es
        from app.services import intercompany as ic
        from app.services import parquet_service

        def _copy(*, local_path: str, object_name: str) -> None:
            target = self.root / object_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(local_path, target)

        def _create_run(**kw):
            self.runs += 1
            return f"run-{self.runs}"

        monkeypatch.setattr(parquet_service, "upload_file_to_minio", _copy)
        for module in (es, ic):
            monkeypatch.setattr(module, "create_run", _create_run)
            monkeypatch.setattr(module, "finish_run", lambda **kw: None)
            monkeypatch.setattr(module, "fail_run", lambda **kw: self.failed.append(kw))
        monkeypatch.setattr(es, "get_watermark", lambda key: self.watermarks.get(key))
        monkeypatch.setattr(es, "update_watermark", lambda **kw: self.watermarks.__setitem__(kw["entity_name"], kw["last_watermark_value"]))
        monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: datetime(2099, 1, 1))


def _configs() -> list[dict]:
    from app.services.catalog_service import _yaml_entities

    return [dict(e) for e in _yaml_entities()]


def _extract(entities: list[str] | None = None, mode: str | None = None) -> None:
    from app.services import extraction_service as es
    from app.services import intercompany as ic

    for config in _configs():
        if entities is not None and config["entity"] not in entities:
            continue
        if mode:
            config["mode"] = mode
        es.run_entity(config)
    if entities is None:
        ic.refresh_intercompany_partners()


def _dataset_files() -> list[Path]:
    return sorted(DATASETS.glob("*.sql"))


def _layer(path: Path) -> str:
    return HEADER_RE.match(path.read_text(encoding="utf-8").splitlines()[0]).group(2)


def _materialise(bronze: Path):
    import duckdb

    con = duckdb.connect()
    silver_root = bronze / "silver" / "sap_b1"
    files = _dataset_files()
    for path in [p for p in files if _layer(p) == "silver"] + [p for p in files if _layer(p) == "gold"]:
        sql = path.read_text(encoding="utf-8").replace("s3://{bucket}/", bronze.as_posix() + "/")
        out = silver_root / path.stem
        out.mkdir(parents=True, exist_ok=True)
        con.execute(f"CREATE OR REPLACE TABLE \"{path.stem}\" AS {sql}")
        con.execute(f"COPY \"{path.stem}\" TO '{(out / 'data.parquet').as_posix()}' (FORMAT PARQUET)")
    return con


def _rows(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def _month_map(rows) -> dict[tuple, Decimal]:
    return {tuple(r[:-1]): Decimal(str(r[-1])) for r in rows}


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pg(dsn: str, sql: str, params=None):
    import psycopg2

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _pg_exec(dsn: str, sql: str, params=None) -> None:
    import psycopg2

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()


def _schema(dataset, alias: str) -> str:
    return next(c.schema for c in dataset.companies if c.alias == alias)


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
    bronze = Bronze(root)
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
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."LineTotal") FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        cost = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."StockPrice" * l."Quantity") FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1', ("N",)))
        profit = _month_map(_pg(dsn,
            f'SELECT DATE_TRUNC(\'month\', h."DocDate")::date, SUM(l."GrssProfit") FROM "{s}"."OINV" h JOIN "{s}"."INV1" l ON l."DocEntry" = h."DocEntry" '
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

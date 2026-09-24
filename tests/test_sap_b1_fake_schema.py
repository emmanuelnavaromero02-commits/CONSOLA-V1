"""The Business One-shaped Postgres fake: it loads, and its numbers add up.

This is the test bed for the future `sap_b1` cartridge. Nothing here talks to
SAP: the schema and the data come from `tests/fixtures/sap_b1`, which records
a ground truth while it generates. The tests below prove the fake is worth
trusting before any extraction SQL is written against it:

* it is deterministic, so a failing test can be replayed byte for byte, and
  its invariants hold for several seeds, not just the default one;
* every document carries doc, local and system currency amounts, never a
  null currency; `DocRate` is 0 on a local-currency document as B1 stores
  it, and the system amounts follow the daily rate in `ORTT`;
* the DDL applies to a real Postgres 15 and the loaded totals match the
  ground truth: invoice lines, the revenue account in the journal, cost of
  goods, closing stock from the movements in date order, batch quantities
  from the batch transactions, and the intercompany sales of the
  manufacturer mirrored as its distributors' purchases month by month;
* journal entries balance in both currencies, cancellations exist in both
  B1 shapes ('Y' on the original, 'C' on the cancellation document) and
  `UpdateTS` stays a valid HHMMSS integer.

The Postgres tests need Docker and skip cleanly without it, following the
pattern of the other live-Postgres tests in this directory. In CI they run
in the job that pre-pulls `postgres:15`.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "sap_b1"
POSTGRES_IMAGE = os.getenv("SAP_B1_FAKE_POSTGRES_IMAGE", "postgres:15")
POSTGRES_PASSWORD = "test_sap_b1_fake_password"


def _load_fixture_package():
    """Import tests/fixtures/sap_b1 as a package without touching sys.path globally."""
    name = "sap_b1_fake"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, FIXTURE_DIR / "__init__.py", submodule_search_locations=[str(FIXTURE_DIR)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return package


_pkg = _load_fixture_package()
b1 = importlib.import_module("sap_b1_fake.schema")
generator = importlib.import_module("sap_b1_fake.generator")
loader = importlib.import_module("sap_b1_fake.loader")

OTHER_SEEDS = (0, 1, 5, 6, 11)


@pytest.fixture(scope="module")
def dataset():
    return generator.generate(seed=7, start_month=date(2024, 10, 1), months=24)


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


# ── the generator, no Postgres needed ──────────────────────────────────────


def test_generation_is_deterministic():
    """Same seed, same bytes: a failing test can always be replayed."""
    first = generator.generate(seed=11, months=6)
    second = generator.generate(seed=11, months=6)
    assert first.checksum() == second.checksum()
    assert first.truth_as_dict() == second.truth_as_dict()
    assert generator.generate(seed=12, months=6).checksum() != first.checksum()


def test_every_table_is_populated_for_every_company(dataset):
    empty = {
        (alias, table)
        for alias, tables in dataset.tables.items()
        for table in b1.TABLES
        if not tables.get(table)
        # A distributor neither produces nor buys raw material; a manufacturer
        # has no A/P credit memos or returns in this model.
        and not (table in ("OWOR", "WOR1", "OITT", "ITT1") and alias != "mx_mfg")
        and table not in ("ORPC", "RPC1", "ORDN", "RDN1")
    }
    assert not empty, f"tables with no rows: {sorted(empty)}"


def test_rows_match_the_declared_columns(dataset):
    for alias, tables in dataset.tables.items():
        for table, rows in tables.items():
            width = len(b1.columns(table))
            bad = [row for row in rows if len(row) != width]
            assert not bad, f"{alias}.{table}: {len(bad)} rows do not match the {width} declared columns"


def test_documents_never_carry_a_null_currency(dataset):
    """The rule the future datasets inherit: doc, local and system currency, always."""
    for alias, tables in dataset.tables.items():
        for header, line, _obj in b1.MARKETING_PAIRS:
            hcols = b1.columns(header)
            for row in tables[header]:
                assert row[hcols.index("DocCur")], f"{alias}.{header} DocEntry={row[0]} has no DocCur"
                assert row[hcols.index("DocRate")] is not None
                assert row[hcols.index("DocTotalSy")] is not None
            lcols = b1.columns(line)
            for row in tables[line]:
                assert row[lcols.index("Currency")], f"{alias}.{line} has a line without Currency"
                assert row[lcols.index("TotalSumSy")] is not None


def test_local_currency_documents_carry_docrate_zero_and_sys_amounts_by_rate(dataset):
    """B1 stores DocRate = 0 on a document in the company's local currency; the
    system-currency amount is not DocTotal / DocRate but DocTotal / ORTT rate.
    A query that divides by DocRate must guard the zero, as B1 queries do."""
    for company in dataset.companies:
        tables = dataset.tables[company.alias]
        rcols = b1.columns("ORTT")
        rates = {(row[rcols.index("RateDate")].date(), row[rcols.index("Currency")]): row[rcols.index("Rate")] for row in tables["ORTT"]}
        hcols = b1.columns("OINV")
        lcols = b1.columns("INV1")
        for row in tables["OINV"]:
            assert row[hcols.index("DocCur")] == company.local_currency
            assert row[hcols.index("DocRate")] == 0
            if company.sys_currency == company.local_currency:
                assert row[hcols.index("DocTotalSy")] == row[hcols.index("DocTotal")]
            else:
                rate = rates[(row[hcols.index("DocDate")].date(), company.sys_currency)]
                expected = (row[hcols.index("DocTotal")] / rate).quantize(Decimal("0.000001"))
                assert row[hcols.index("DocTotalSy")] == expected
        assert all(row[lcols.index("Rate")] == 0 for row in tables["INV1"])
    usd = next(c for c in dataset.companies if c.sys_currency != c.local_currency)
    assert len(dataset.tables[usd.alias]["OINV"]) > 100


def test_exchange_rates_are_daily(dataset):
    rcols = b1.columns("ORTT")
    for alias, tables in dataset.tables.items():
        days = sorted(row[rcols.index("RateDate")].date() for row in tables["ORTT"])
        assert days[0] < dataset.start_month and days[-1] == dataset.as_of
        assert len(days) == (days[-1] - days[0]).days + 1, f"{alias}: ORTT must have one row per day"


def test_cancellations_come_in_both_b1_shapes(dataset):
    hcols = b1.columns("OINV")
    lcols = b1.columns("INV1")
    for alias, tables in dataset.tables.items():
        flags = [row[hcols.index("CANCELED")] for row in tables["OINV"]]
        assert set(flags) <= {"N", "Y", "C"}
        assert "Y" in flags and "C" in flags, f"{alias}: expected cancelled originals and cancellation documents"
        assert flags.count("Y") == flags.count("C")
        cancelled = {row[hcols.index("DocEntry")] for row in tables["OINV"] if row[hcols.index("CANCELED")] == "Y"}
        cancellation_docs = {row[hcols.index("DocEntry")] for row in tables["OINV"] if row[hcols.index("CANCELED")] == "C"}
        # The cancellation document's lines are drawn from the original invoice.
        based_on = {row[lcols.index("BaseEntry")] for row in tables["INV1"] if row[lcols.index("DocEntry")] in cancellation_docs}
        assert based_on == cancelled, f"{alias}: cancellation documents must point at the cancelled invoices"


def test_update_stamps_are_valid(dataset):
    for alias, tables in dataset.tables.items():
        for header, _line, _obj in b1.MARKETING_PAIRS:
            cols = b1.columns(header)
            for row in tables[header]:
                update_ts = row[cols.index("UpdateTS")]
                assert 0 <= update_ts <= b1.UPDATE_TS_MAX, f"{alias}.{header}: UpdateTS {update_ts} is not HHMMSS"
                assert row[cols.index("UpdateDate")] >= row[cols.index("CreateDate")]
                assert row[cols.index("DocDate")].date() <= dataset.as_of
                assert row[cols.index("UpdateDate")].date() <= dataset.as_of


def test_journal_entries_balance_in_both_currencies(dataset):
    """Every TransId sums to zero in local and in system currency."""
    jcols = b1.columns("JDT1")
    for alias, tables in dataset.tables.items():
        local = defaultdict(Decimal)
        system = defaultdict(Decimal)
        for row in tables["JDT1"]:
            trans = row[jcols.index("TransId")]
            local[trans] += row[jcols.index("Debit")] - row[jcols.index("Credit")]
            system[trans] += row[jcols.index("SYSDeb")] - row[jcols.index("SYSCred")]
        assert all(v == 0 for v in local.values()), f"{alias}: unbalanced journal in local currency"
        assert all(v == 0 for v in system.values()), f"{alias}: unbalanced journal in system currency"


def test_partner_lines_carry_the_card_code_as_shortname(dataset):
    """B1 puts the business partner code in JDT1.ShortName on control-account lines."""
    jcols = b1.columns("JDT1")
    for alias, tables in dataset.tables.items():
        for row in tables["JDT1"]:
            account, short = row[jcols.index("Account")], row[jcols.index("ShortName")]
            if account in (generator.ACCT_AR, generator.ACCT_AP):
                assert short != account and short.startswith(("C-", "S-", "V-")), f"{alias}: {account} line without CardCode"
            else:
                assert short == account


def test_stock_never_goes_negative_in_date_order(dataset):
    """Replaying OINM by (DocDate, TransNum, TransSeq) never dips below zero: no stock
    as-of query can ever see a negative balance."""
    icols = b1.columns("OINM")
    for alias, tables in dataset.tables.items():
        balance = defaultdict(Decimal)
        rows = sorted(tables["OINM"], key=lambda r: (r[icols.index("DocDate")], r[icols.index("TransNum")], r[icols.index("TransSeq")]))
        for row in rows:
            key = (row[icols.index("ItemCode")], row[icols.index("Warehouse")])
            balance[key] += row[icols.index("InQty")] - row[icols.index("OutQty")]
            assert balance[key] >= 0, f"{alias}: {key} negative on {row[icols.index('DocDate')].date()}"


def test_stock_transactions_share_a_transnum_per_document(dataset):
    """B1 numbers one stock transaction per document: its lines share TransNum
    and are told apart by TransSeq. A cartridge that pages on TransNum alone
    would drop lines at every page cut; the fake has to make that visible."""
    icols = b1.columns("OINM")
    for alias, tables in dataset.tables.items():
        rows = tables["OINM"]
        keys = [(row[icols.index("TransNum")], row[icols.index("TransSeq")]) for row in rows]
        assert len(keys) == len(set(keys)), f"{alias}: (TransNum, TransSeq) must be unique"
        per_trans = defaultdict(list)
        for row in rows:
            per_trans[row[icols.index("TransNum")]].append(row)
        multi = [t for t, group in per_trans.items() if len(group) > 1]
        assert multi, f"{alias}: expected multi-line stock transactions"
        for trans in multi:
            group = per_trans[trans]
            assert {(r[icols.index("TransType")], r[icols.index("CreatedBy")]) for r in group} .__len__() == 1
            assert sorted(r[icols.index("TransSeq")] for r in group) == list(range(len(group)))
    lcols = b1.columns("IBT1")
    for alias, tables in dataset.tables.items():
        entries = [row[lcols.index("LogEntry")] for row in tables["IBT1"]]
        assert entries == sorted(entries) and len(entries) == len(set(entries)), f"{alias}: IBT1.LogEntry must be a growing identity"


def test_intercompany_truth_is_recorded_on_both_sides_month_by_month(dataset):
    """Manufacturer's sales to a distributor == that distributor's purchases from it, every month."""
    for alias in ("mx_dist_a", "mx_dist_b"):
        months = sorted(set(dataset.truth["mx_mfg"]) | set(dataset.truth[alias]))
        for month in months:
            sold = dataset.truth["mx_mfg"].get(month, generator.MonthTruth()).intercompany_sales_lc.get(alias, Decimal(0))
            bought = dataset.truth[alias].get(month, generator.MonthTruth()).intercompany_purchases_lc
            assert sold == bought, f"{alias} {month}: manufacturer sold {sold}, distributor booked {bought}"
        assert sum((m.intercompany_purchases_lc for m in dataset.truth[alias].values()), Decimal(0)) > 0


@pytest.mark.parametrize("seed", OTHER_SEEDS)
def test_invariants_hold_for_other_seeds(seed):
    """The suite must not pass by luck of the default seed."""
    ds = generator.generate(seed=seed, months=24)
    icols = b1.columns("OINM")
    hcols = b1.columns("OINV")
    for alias, tables in ds.tables.items():
        balance = defaultdict(Decimal)
        for row in sorted(tables["OINM"], key=lambda r: (r[icols.index("DocDate")], r[icols.index("TransNum")], r[icols.index("TransSeq")])):
            key = (row[icols.index("ItemCode")], row[icols.index("Warehouse")])
            balance[key] += row[icols.index("InQty")] - row[icols.index("OutQty")]
            assert balance[key] >= 0, f"seed {seed} {alias}: negative stock for {key}"
        for header, _line, _obj in b1.MARKETING_PAIRS:
            cols = b1.columns(header)
            for row in tables[header]:
                assert row[cols.index("DocDate")].date() <= ds.as_of, f"seed {seed} {alias}.{header}: document after as_of"
                assert row[cols.index("UpdateDate")].date() <= ds.as_of
        flags = [row[hcols.index("CANCELED")] for row in tables["OINV"]]
        assert flags.count("Y") == flags.count("C")
    for alias in ("mx_dist_a", "mx_dist_b"):
        for month in set(ds.truth["mx_mfg"]) | set(ds.truth[alias]):
            sold = ds.truth["mx_mfg"].get(month, generator.MonthTruth()).intercompany_sales_lc.get(alias, Decimal(0))
            bought = ds.truth[alias].get(month, generator.MonthTruth()).intercompany_purchases_lc
            assert sold == bought, f"seed {seed} {alias} {month}: intercompany mismatch"


# ── the schema on a real Postgres ──────────────────────────────────────────


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def _mapped_port(container_id: str) -> int:
    for _ in range(60):
        result = _docker("port", container_id, "5432/tcp", check=False)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
        time.sleep(0.5)
    raise RuntimeError("postgres port was not published")


def _wait_ready(dsn: str) -> None:
    import psycopg2

    last: Exception | None = None
    for _ in range(120):
        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001 - startup race, retried
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"postgres never became ready: {last}")


@pytest.fixture(scope="module")
def loaded_dsn(dataset):
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is required to run the Postgres-backed B1 fake")
    if _docker("image", "inspect", POSTGRES_IMAGE, check=False).returncode != 0:
        pytest.skip(f"{POSTGRES_IMAGE} is not present locally (docker pull it first)")
    name = f"consola-sap-b1-fake-{uuid.uuid4().hex[:12]}"
    container = _docker(
        "run", "--pull=never", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_DB=b1fake", "-e", "POSTGRES_USER=postgres", "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-P", POSTGRES_IMAGE,
    ).stdout.strip()
    try:
        port = _mapped_port(container)
        dsn = f"postgresql://postgres:{POSTGRES_PASSWORD}@127.0.0.1:{port}/b1fake"
        _wait_ready(dsn)
        loader.load(dsn, dataset)
        yield dsn
    finally:
        _docker("rm", "-f", container, check=False)


def _schema(dataset, alias: str) -> str:
    return next(c.schema for c in dataset.companies if c.alias == alias)


def test_ddl_applies_and_row_counts_match(dataset, loaded_dsn):
    for company in dataset.companies:
        for table, rows in dataset.tables[company.alias].items():
            count = loader.scalar(loaded_dsn, f"SELECT COUNT(*) FROM {loader.table_ref(company.schema, table)}")
            assert count == len(rows), f"{company.schema}.{table}: loaded {count}, generated {len(rows)}"


def test_hana_style_quoted_query_returns_what_the_generator_wrote(dataset, loaded_dsn):
    """The fake is addressed exactly like HANA: "SCHEMA"."TABLE"."Column", and an
    incremental predicate on UpdateDate returns the same rows the generator holds."""
    schema = _schema(dataset, "mx_mfg")
    since = date(2026, 6, 1)
    rows = loader.query(
        loaded_dsn,
        f'SELECT "DocEntry", "CardCode", "DocTotal" FROM {loader.table_ref(schema, "OINV")} '
        f'WHERE "CANCELED" = %s AND "UpdateDate" >= %s ORDER BY "DocEntry"',
        ("N", since),
    )
    hcols = b1.columns("OINV")
    expected = sorted(
        (r[hcols.index("DocEntry")], r[hcols.index("CardCode")], r[hcols.index("DocTotal")])
        for r in dataset.tables["mx_mfg"]["OINV"]
        if r[hcols.index("CANCELED")] == "N" and r[hcols.index("UpdateDate")].date() >= since
    )
    assert rows == expected
    assert 0 < len(rows) < len(dataset.tables["mx_mfg"]["OINV"])


def test_invoice_lines_and_revenue_account_match_the_truth(dataset, loaded_dsn):
    """Reconciliation the client will ask for: documents vs. accounting, exact."""
    for company in dataset.companies:
        s = company.schema
        lines = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM h."DocDate"), EXTRACT(MONTH FROM h."DocDate"), SUM(l."LineTotal") '
            f'FROM {loader.table_ref(s, "OINV")} h JOIN {loader.table_ref(s, "INV1")} l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1, 2', ("N",),
        ))
        credits = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM h."DocDate"), EXTRACT(MONTH FROM h."DocDate"), SUM(l."LineTotal") '
            f'FROM {loader.table_ref(s, "ORIN")} h JOIN {loader.table_ref(s, "RIN1")} l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CANCELED" = %s GROUP BY 1, 2', ("N",),
        ))
        revenue = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM j."RefDate"), EXTRACT(MONTH FROM j."RefDate"), SUM(j."Credit" - j."Debit") '
            f'FROM {loader.table_ref(s, "JDT1")} j WHERE j."Account" = %s GROUP BY 1, 2', (generator.ACCT_REVENUE,),
        ))
        cogs = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM j."RefDate"), EXTRACT(MONTH FROM j."RefDate"), SUM(j."Debit" - j."Credit") '
            f'FROM {loader.table_ref(s, "JDT1")} j WHERE j."Account" = %s GROUP BY 1, 2', (generator.ACCT_COGS,),
        ))
        truth = dataset.truth[company.alias]
        for month, t in truth.items():
            assert lines.get(month, Decimal(0)) == t.revenue_gross_lc, f"{company.alias} {month}: invoice lines"
            assert credits.get(month, Decimal(0)) == t.credit_lc, f"{company.alias} {month}: credit memos"
            # Month by month the journal follows the cancellation date, not the
            # invoice date; the documents view drops a cancelled invoice from
            # its own month. Both are right, and the period totals agree.
            assert revenue.get(month, Decimal(0)) == t.revenue_account_lc, f"{company.alias} {month}: revenue account"
            assert cogs.get(month, Decimal(0)) == t.cogs_lc, f"{company.alias} {month}: cost of goods"
        period_documents = sum((t.revenue_net_lc for t in truth.values()), Decimal(0))
        period_accounting = sum(revenue.values(), Decimal(0))
        assert period_documents == period_accounting, f"{company.alias}: documents vs accounting over the period"
        assert sum(t.invoices_canceled for t in truth.values()) == sum(t.cancellation_docs for t in truth.values())


def test_system_currency_in_the_journal_matches_the_truth(dataset, loaded_dsn):
    company = next(c for c in dataset.companies if c.sys_currency != c.local_currency)
    s = company.schema
    revenue_sc = loader.month_key(loader.query(
        loaded_dsn,
        f'SELECT EXTRACT(YEAR FROM j."RefDate"), EXTRACT(MONTH FROM j."RefDate"), SUM(j."SYSCred" - j."SYSDeb") '
        f'FROM {loader.table_ref(s, "JDT1")} j WHERE j."Account" = %s GROUP BY 1, 2', (generator.ACCT_REVENUE,),
    ))
    for month, t in dataset.truth[company.alias].items():
        assert revenue_sc.get(month, Decimal(0)) == t.revenue_account_sc, f"{month}: system-currency revenue"
    unbalanced = loader.scalar(
        loaded_dsn,
        f'SELECT COUNT(*) FROM (SELECT "TransId" FROM {loader.table_ref(s, "JDT1")} GROUP BY "TransId" '
        f'HAVING SUM("SYSDeb") <> SUM("SYSCred")) u',
    )
    assert unbalanced == 0


def test_closing_stock_equals_the_sum_of_movements_and_the_batches(dataset, loaded_dsn):
    for company in dataset.companies:
        s = company.schema
        movements = {
            (item, whs): net for item, whs, net in loader.query(
                loaded_dsn,
                f'SELECT "ItemCode", "Warehouse", SUM("InQty" - "OutQty") FROM {loader.table_ref(s, "OINM")} GROUP BY 1, 2',
            )
        }
        on_hand = {
            (item, whs): qty for item, whs, qty in loader.query(
                loaded_dsn, f'SELECT "ItemCode", "WhsCode", "OnHand" FROM {loader.table_ref(s, "OITW")}'
            )
        }
        assert movements == on_hand, f"{company.alias}: OITW disagrees with OINM"
        assert all(qty >= 0 for qty in on_hand.values()), f"{company.alias}: negative stock"
        batch_stock = {
            (item, whs): qty for item, whs, qty in loader.query(
                loaded_dsn,
                f'SELECT "ItemCode", "WhsCode", SUM("Quantity") FROM {loader.table_ref(s, "OBTQ")} GROUP BY 1, 2',
            )
        }
        for key, qty in batch_stock.items():
            assert qty == on_hand[key], f"{company.alias}: batch quantities of {key} disagree with OITW"
        # Batch transactions net to the batch quantity per (item, batch, warehouse),
        # through OBTN's DistNumber, and OIBT says the same.
        mismatched = loader.scalar(
            loaded_dsn,
            f'SELECT COUNT(*) FROM ('
            f'  SELECT t."ItemCode", t."BatchNum", t."WhsCode", '
            f'         SUM(CASE WHEN t."Direction" = 0 THEN t."Quantity" ELSE -t."Quantity" END) AS net '
            f'  FROM {loader.table_ref(s, "IBT1")} t GROUP BY 1, 2, 3) x '
            f'JOIN {loader.table_ref(s, "OBTN")} n ON n."ItemCode" = x."ItemCode" AND n."DistNumber" = x."BatchNum" '
            f'JOIN {loader.table_ref(s, "OBTQ")} q ON q."ItemCode" = n."ItemCode" AND q."SysNumber" = n."SysNumber" AND q."WhsCode" = x."WhsCode" '
            f'JOIN {loader.table_ref(s, "OIBT")} b ON b."ItemCode" = x."ItemCode" AND b."BatchNum" = x."BatchNum" AND b."WhsCode" = x."WhsCode" '
            f'WHERE x.net <> q."Quantity" OR b."Quantity" <> q."Quantity"',
        )
        assert mismatched == 0, f"{company.alias}: batch transactions do not net to batch quantities"
        duplicate_batches = loader.scalar(
            loaded_dsn,
            f'SELECT COUNT(*) FROM (SELECT "ItemCode", "DistNumber" FROM {loader.table_ref(s, "OBTN")} '
            f'GROUP BY 1, 2 HAVING COUNT(*) > 1) d',
        )
        assert duplicate_batches == 0, f"{company.alias}: a batch number must be unique per item"


def test_intercompany_sales_are_the_distributors_purchases(dataset, loaded_dsn):
    """What consolidation must eliminate, provable on both sides of the group, month by month."""
    mfg = _schema(dataset, "mx_mfg")
    for alias, customer in sorted(generator.INTERCOMPANY_CUSTOMER.items()):
        dist = _schema(dataset, alias)
        sold = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM h."DocDate"), EXTRACT(MONTH FROM h."DocDate"), SUM(l."LineTotal") '
            f'FROM {loader.table_ref(mfg, "OINV")} h JOIN {loader.table_ref(mfg, "INV1")} l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CardCode" = %s AND h."CANCELED" = %s GROUP BY 1, 2', (customer, "N"),
        ))
        bought = loader.month_key(loader.query(
            loaded_dsn,
            f'SELECT EXTRACT(YEAR FROM h."DocDate"), EXTRACT(MONTH FROM h."DocDate"), SUM(l."LineTotal") '
            f'FROM {loader.table_ref(dist, "OPCH")} h JOIN {loader.table_ref(dist, "PCH1")} l ON l."DocEntry" = h."DocEntry" '
            f'WHERE h."CardCode" = %s AND h."CANCELED" = %s GROUP BY 1, 2', (generator.INTERCOMPANY_SUPPLIER, "N"),
        ))
        assert sold == bought, f"{alias}: intercompany sales and purchases differ by month"
        assert sum(sold.values(), Decimal(0)) > 0


def test_document_chains_are_linked(dataset, loaded_dsn):
    """Order lines point at their delivery, delivery lines at their invoice."""
    s = _schema(dataset, "mx_mfg")
    unlinked = loader.scalar(
        loaded_dsn,
        f'SELECT COUNT(*) FROM {loader.table_ref(s, "DLN1")} d '
        f'LEFT JOIN {loader.table_ref(s, "OINV")} i ON i."DocEntry" = d."TrgetEntry" '
        f'WHERE d."TargetType" <> 13 OR i."DocEntry" IS NULL',
    )
    assert unlinked == 0
    open_lines = loader.scalar(loaded_dsn, f'SELECT COUNT(*) FROM {loader.table_ref(s, "RDR1")} WHERE "LineStatus" <> %s', ("C",))
    assert open_lines == 0


def test_expired_batches_exist_for_the_expiry_agent(dataset, loaded_dsn):
    for company in dataset.companies:
        s = company.schema
        expired = loader.scalar(
            loaded_dsn,
            f'SELECT COUNT(*) FROM {loader.table_ref(s, "OBTN")} n JOIN {loader.table_ref(s, "OBTQ")} q '
            f'ON q."ItemCode" = n."ItemCode" AND q."SysNumber" = n."SysNumber" '
            f'WHERE q."Quantity" > 0 AND n."ExpDate" < %s', (dataset.as_of,),
        )
        assert expired == dataset.expired_batches[company.alias]
        assert expired > 0, f"{company.alias}: the expiry agent needs something to find"

from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import logging
import re
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlglot
from cryptography.fernet import Fernet

import refinement.app.successfactors_exposure_materializer as exposure
from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.publication_contract import PublicationIdentity
from refinement.app.publication_inputs import resolve_input_state


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SQL = (
    REPO_ROOT
    / "cartridges/sap_successfactors/datasets"
    / "sap_successfactors_talent_attrition_exposure.sql"
)
CTX = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
TODAY = exposure._utc_today()
STARTED = TODAY - timedelta(days=40)
OPEN_END = date(9999, 12, 31)
CONTRACT_COLUMNS = [name for name, _ in exposure.OUTPUT_COLUMNS] + list(
    exposure.CONSTANT_COLUMNS
)
RISK_COLUMNS = (
    ("user_id", "VARCHAR"),
    ("department_name", "VARCHAR"),
    ("risk_band", "VARCHAR"),
    ("invalid_score_input", "BOOLEAN"),
    ("retention_risk_score", "DOUBLE"),
)
RECURRING_COLUMNS = (
    ("user_id", "VARCHAR"),
    ("pay_component", "VARCHAR"),
    ("paycomp_value", "VARCHAR"),
    ("frequency", "VARCHAR"),
    ("currency", "VARCHAR"),
    ("start_date", "DATE"),
    ("end_date", "DATE"),
    ("load_date", "VARCHAR"),
)
NON_RECURRING_COLUMNS = (
    ("user_id", "VARCHAR"),
    ("pay_component", "VARCHAR"),
    ("paycomp_value", "VARCHAR"),
    ("currency", "VARCHAR"),
    ("pay_date", "DATE"),
)


def _duckdb():
    existing = sys.modules.get("duckdb")
    if isinstance(existing, MagicMock) or isinstance(
        getattr(existing, "connect", None), MagicMock
    ):
        sys.modules.pop("duckdb", None)
    return importlib.import_module("duckdb")


class RecordingConnection:
    def __init__(self) -> None:
        self._con = _duckdb().connect()
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(str(sql))
        return self._con.execute(sql, *args)

    def create_function(self, *_args, **_kwargs):
        raise AssertionError("decryption must never be registered as a SQL function")

    def register(self, *_args, **_kwargs):
        raise AssertionError("no relation may be registered on the shared connection")

    def __getattr__(self, name):
        return getattr(self._con, name)


class LocalEngine(DuckDBEngine):
    def __init__(self, tmp_path: Path, uris: dict[str, str]) -> None:
        super().__init__()
        self._local = RecordingConnection()
        self._tmp = tmp_path
        self._uris = uris
        self.published: dict = {}

    def _conn(self):
        return self._local

    def _latest_materialized_uri(self, layer, cartridge, name, user_context=None):
        return self._uris.get(f"{layer}/{cartridge}/{name}")

    def _silver_path(self, cartridge, name, user_context=None):
        return str(self._tmp / "missing" / f"{name}.parquet")

    def _gold_path(self, cartridge, name, user_context=None):
        return str(self._tmp / "missing" / f"{name}.parquet")

    def _snapshot_path(self, layer, cartridge, name, user_context=None):
        return f"s3://lakehouse/{layer}/{cartridge}/{name}/snapshot.parquet"

    def _pg_gold_attach(self, con, user_context=None):
        return "pggold"

    def _ensure_scoped_gold_table(self, con, table, sql):
        self.published["table"] = table

    def _replace_scoped_gold_rows(self, con, table, sql, tenant, workspace):
        result = con.execute(f"SELECT * FROM ({sql}) _q")  # nosec B608 - materializer output
        columns = [item[0] for item in result.description]
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        self.published.update(columns=columns, rows=rows, scope=(tenant, workspace))
        return len(rows)

    def _apply_gold_rls(self, table):
        self.published["rls"] = table

    def _copy_scoped_gold_table_snapshot(
        self, con, table, storage_path, tenant, workspace, user_context, partition_by=None
    ):
        return storage_path

    def _write_lineage(self, **values):
        self.published["lineage"] = values

    def _prune_snapshots(self, *args, **kwargs):
        return None

    def _update_catalog(self, *args, **kwargs):
        self.published["catalog"] = args


def _write(path: Path, columns, rows) -> str:
    con = _duckdb().connect()
    try:
        con.execute(
            "CREATE TABLE src (" + ", ".join(f"{n} {t}" for n, t in columns) + ")"
        )
        if rows:
            marks = ", ".join("?" for _ in columns)
            con.executemany(f"INSERT INTO src VALUES ({marks})", rows)  # nosec B608 - placeholders only
        con.execute(f"COPY src TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()
    return str(path)


def _risk(user_id, department, band="high", invalid=False, score=80.0):
    return (user_id, department, band, invalid, score)


def _pay(fernet, user_id, amount, *, frequency="MON", currency="MXN", component="BASE",
         start=STARTED, end=OPEN_END, token=None):
    value = token if token is not None else fernet.encrypt(amount.encode()).decode()
    return (user_id, component, value, frequency, currency, start, end, "2026-09-01")


def _engine(tmp_path, risk_rows, recurring_rows, non_recurring_rows=None):
    uris = {
        exposure.RISK_SOURCE: _write(tmp_path / "risk.parquet", RISK_COLUMNS, risk_rows),
        exposure.RECURRING_SOURCE: _write(
            tmp_path / "recurring.parquet", RECURRING_COLUMNS, recurring_rows
        ),
    }
    if non_recurring_rows is not None:
        uris[exposure.NON_RECURRING_SOURCE] = _write(
            tmp_path / "non_recurring.parquet", NON_RECURRING_COLUMNS, non_recurring_rows
        )
    return LocalEngine(tmp_path, uris)


def _dataset():
    return {
        "name": exposure.EXPOSURE_DATASET,
        "layer": "gold",
        "cartridge": "sap_successfactors",
        "sources": list(exposure.EXPOSURE_SOURCES),
        "sql_def": CONTRACT_SQL.read_text(encoding="utf-8"),
    }


@pytest.fixture()
def key(monkeypatch):
    value = Fernet.generate_key().decode()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", value)
    return value


def _by_group(rows):
    return {(r["risk_band"], r["currency"]): r for r in rows}


def _assert_avg_derives_from_published_total(rows):
    for row in rows:
        assert row["annualized_comp_avg"] == exposure.round_significant(
            row["annualized_comp_total"] / row["headcount"]
        )


def test_publishes_only_groups_of_five_with_annualized_aggregates(tmp_path, key):
    fernet = Fernet(key.encode())
    monthly = {f"v-{i}": Decimal("10000.13") + Decimal("7.29") * i for i in range(6)}
    risk = [_risk(uid, "Ventas") for uid in monthly]
    risk += [_risk(f"c-{i}", "Chica", "low") for i in range(4)]
    risk += [_risk(f"m-{i}", "Mixta", "medium") for i in range(7)]
    pays = [_pay(fernet, uid, str(amount)) for uid, amount in monthly.items()]
    pays += [_pay(fernet, f"c-{i}", "90000", frequency="ANN") for i in range(4)]
    pays += [_pay(fernet, f"m-{i}", "50000.5", frequency="ANN") for i in range(7)]
    pays.append(_pay(fernet, "m-0", "3000", currency="USD", component="EXPAT"))
    engine = _engine(tmp_path, risk, pays)

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    rows = engine.published["rows"]
    assert result["row_count"] == 2 == len(rows)
    assert engine.published["columns"] == ["tenant_id", "workspace_id", *CONTRACT_COLUMNS]
    assert engine.published["scope"] == ("tenant-a", "workspace-a")
    groups = _by_group(rows)
    assert set(groups) == {("high", "MXN"), ("medium", "MXN")}
    expected_total = sum(amount * 12 for amount in monthly.values())
    assert expected_total == Decimal("721321.56")
    high = groups[("high", "MXN")]
    assert high["headcount"] == 6
    assert high["annualized_comp_total"] == Decimal("1000000.00")
    assert high["annualized_comp_avg"] == Decimal("170000.00")
    medium = groups[("medium", "MXN")]
    assert medium["headcount"] == 7
    assert medium["annualized_comp_total"] == Decimal("400000.00")
    assert medium["annualized_comp_avg"] == Decimal("57000.00")
    _assert_avg_derives_from_published_total(rows)
    assert "department_name" not in engine.published["columns"]
    assert all(row["headcount"] >= 5 for row in rows)
    assert all(row["privacy_rule"] == "aggregate_min5_dominance50_unitmax" for row in rows)
    assert all(row["contract_version"] == "talent_attrition_exposure.v4" for row in rows)
    assert all(row["tenant_id"] == "tenant-a" for row in rows)


def test_output_has_no_employee_key_or_per_person_value(tmp_path, key):
    fernet = Fernet(key.encode())
    amounts = ["31017.17", "33129.58", "35211.03", "38877.91", "41003.64"]
    risk = [_risk(f"secret-user-{i}", "Planta") for i in range(5)]
    pays = [_pay(fernet, f"secret-user-{i}", amount) for i, amount in enumerate(amounts)]
    engine = _engine(tmp_path, risk, pays)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    published = json.dumps(engine.published["rows"], default=str)
    assert "user_id" not in engine.published["columns"]
    assert "secret-user" not in published
    for amount in amounts:
        assert amount not in published
        assert str(Decimal(amount) * 12) not in published


def test_unknown_frequency_bad_tokens_and_bad_amounts_are_counted_not_summed(tmp_path, key):
    fernet = Fernet(key.encode())
    other = Fernet(Fernet.generate_key())
    risk = [_risk(f"u-{i}", "Operaciones") for i in range(8)]
    pays = [_pay(fernet, f"u-{i}", "1000") for i in range(5)]
    pays.append(_pay(fernet, "u-5", "1000", frequency="HOURLY"))
    pays.append(_pay(fernet, "u-6", "1000", token=other.encrypt(b"1000").decode()))
    pays.append(_pay(fernet, "u-7", "not-a-number"))
    pays.append(_pay(fernet, "u-0", "", component="ALLOWANCE", token="gAAAAAtampered"))
    pays.append(_pay(fernet, "u-1", "5", frequency=None, component="BONUS_REC"))
    pays.append(_pay(fernet, "u-2", "5", currency=None, component="NOCUR"))
    engine = _engine(tmp_path, risk, pays)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert row["headcount"] == 5
    assert row["annualized_comp_total"] == Decimal("100000.00")
    assert row["excluded_unknown_frequency"] == 2
    assert row["excluded_undecryptable"] == 2
    assert row["excluded_invalid_amount"] == 1
    assert "HOURLY" not in exposure.FREQUENCY_FACTORS


def test_aggregate_counters_report_exclusions(tmp_path, key):
    fernet = Fernet(key.encode())
    records = [
        ("high", "MXN", f"u-{i}", "MON", fernet.encrypt(b"100").decode(), 1)
        for i in range(5)
    ]
    records += [
        ("high", "MXN", "u-9", "DAILY", fernet.encrypt(b"100").decode(), 1),
        ("high", "MXN", "u-8", "MON", "garbage", 1),
        ("high", None, "u-7", "MON", fernet.encrypt(b"100").decode(), 1),
        ("high", "MXN", "u-6", "MON", fernet.encrypt(b"-100").decode(), 1),
        ("low", "MXN", "u-5", "MON", fernet.encrypt(b"100").decode(), 1),
        ("high", "MXN", "u-4", "MON", fernet.encrypt(b"100").decode(), 2),
    ]

    outcome = exposure.aggregate_exposure(iter(records), {("high", "MXN"): 3}, fernet)

    assert outcome.status == exposure.STATUS_PUBLISHED
    assert outcome.counters["records"] == 11
    assert outcome.counters["unknown_frequency"] == 1
    assert outcome.counters["undecryptable"] == 1
    assert outcome.counters["missing_currency"] == 1
    assert outcome.counters["non_positive_total"] == 1
    assert outcome.counters["duplicate_ciphertext"] == 1
    assert outcome.counters["suppressed_small_groups"] == 1
    assert outcome.counters["suppressed_dominance_groups"] == 0
    assert outcome.counters["non_recurring_excluded_365d"] == 3
    (row,) = outcome.rows
    assert row["headcount"] == 5
    assert row["excluded_non_recurring_365d"] == 3
    assert row["annualized_comp_total"] == Decimal("10000.00")
    assert row["annualized_comp_avg"] == Decimal("2000.00")


class _SpyFernet:
    def __init__(self, real: Fernet) -> None:
        self._real = real
        self.tokens: list[str] = []

    def decrypt(self, token: bytes) -> bytes:
        self.tokens.append(token.decode("ascii"))
        return self._real.decrypt(token)


def test_non_recurring_payments_are_counted_in_window_but_never_decrypted(
    tmp_path, key, monkeypatch
):
    fernet = Fernet(key.encode())
    spy = _SpyFernet(fernet)
    monkeypatch.setattr(exposure, "_fernet", lambda _key: spy)
    risk = [_risk(f"u-{i}", "Finanzas") for i in range(5)]
    pays = [_pay(fernet, f"u-{i}", "2000") for i in range(5)]
    bonus_tokens = [fernet.encrypt(b"900000").decode() for _ in range(3)]
    bonuses = [
        (f"u-{i}", "BONUS", bonus_tokens[i], "MXN", TODAY - timedelta(days=10))
        for i in range(3)
    ]
    bonuses += [
        ("u-3", "BONUS", fernet.encrypt(b"1").decode(), "MXN", TODAY - timedelta(days=364)),
        ("u-3", "OLD", fernet.encrypt(b"1").decode(), "MXN", TODAY - timedelta(days=365)),
        ("u-4", "OLD", fernet.encrypt(b"1").decode(), "MXN", date(2015, 1, 1)),
        ("u-4", "NEXT", fernet.encrypt(b"1").decode(), "MXN", TODAY + timedelta(days=3)),
        ("u-4", "NODATE", fernet.encrypt(b"1").decode(), "MXN", None),
    ]
    engine = _engine(tmp_path, risk, pays, bonuses)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert row["annualized_comp_total"] == Decimal("100000.00")
    assert row["excluded_non_recurring_365d"] == 4
    assert row["excluded_undecryptable"] == 0
    assert len(spy.tokens) == 5
    assert not set(spy.tokens) & {bonus[2] for bonus in bonuses}
    counts = [s for s in engine._local.statements if "COUNT(*) AS records" in s]
    assert counts and all("paycomp_value" not in s for s in counts)


def test_only_components_effective_today_are_annualized(tmp_path, key):
    fernet = Fernet(key.encode())
    risk = [_risk(f"u-{i}", "Legal") for i in range(5)]
    pays = [_pay(fernet, f"u-{i}", "1000") for i in range(5)]
    pays.append(
        _pay(fernet, "u-0", "999999", component="BASE", start=TODAY - timedelta(days=400),
             end=STARTED - timedelta(days=1))
    )
    pays.append(
        _pay(fernet, "u-1", "888888", component="ENDED", start=TODAY - timedelta(days=400),
             end=TODAY - timedelta(days=5))
    )
    pays.append(
        _pay(fernet, "u-2", "777777", component="FUTURE", start=TODAY + timedelta(days=5))
    )
    pays.append(_pay(fernet, "u-3", "500", component="OPEN", end=None))
    engine = _engine(tmp_path, risk, pays)
    con = engine._conn()
    reader = {
        source: exposure._reader(engine, engine._uris[source], CTX)
        for source in (exposure.RISK_SOURCE, exposure.RECURRING_SOURCE)
    }
    sql = exposure.recurring_read_sql(
        reader[exposure.RISK_SOURCE], reader[exposure.RECURRING_SOURCE],
        {"invalid_score_input", "retention_risk_score"}, TODAY,
    )

    selected = sorted(
        (row[2], fernet.decrypt(row[4].encode()).decode())
        for row in con.execute(sql).fetchall()
    )

    assert selected == sorted(
        [(f"u-{i}", "1000") for i in range(5)] + [("u-3", "500")]
    )
    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)
    (row,) = engine.published["rows"]
    assert row["headcount"] == 5


def test_employee_with_two_risk_rows_counts_once_in_highest_risk(tmp_path, key):
    fernet = Fernet(key.encode())
    risk = [_risk(f"u-{i}", "Compras", score=75.0) for i in range(5)]
    risk.append(_risk("u-0", "Compras", band="low", score=10.0))
    risk.append(_risk("u-9", "Compras", invalid=True))
    risk.append(_risk("u-8", "Compras", band="insufficient_data"))
    pays = [_pay(fernet, f"u-{i}", "1000") for i in range(5)]
    pays += [_pay(fernet, "u-9", "1000"), _pay(fernet, "u-8", "1000")]
    engine = _engine(tmp_path, risk, pays)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert (row["risk_band"], row["headcount"]) == ("high", 5)
    assert row["annualized_comp_total"] == Decimal("100000.00")


def test_missing_key_publishes_zero_rows_without_reading_compensation(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    caplog.set_level(logging.DEBUG)
    fernet = Fernet(Fernet.generate_key())
    risk = [_risk(f"u-{i}", "Ventas") for i in range(6)]
    engine = _engine(tmp_path, risk, [_pay(fernet, f"u-{i}", "1000") for i in range(6)])

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert result["row_count"] == 0
    assert set(result) == {"name", "layer", "row_count", "storage_uri", "status", "degraded"}
    assert engine.published["rows"] == []
    assert engine.published["columns"] == ["tenant_id", "workspace_id", *CONTRACT_COLUMNS]
    assert not [s for s in engine._local.statements if "recurring.parquet" in s]
    assert "status=missing_key" in caplog.text
    assert (result["status"], result["degraded"]) == ("missing_key", True)


def test_invalid_key_publishes_zero_rows_and_never_logs_it(tmp_path, monkeypatch, caplog):
    bad_key = "not-a-valid-fernet-key-value-0123456789"
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", bad_key)
    caplog.set_level(logging.DEBUG)
    fernet = Fernet(Fernet.generate_key())
    risk = [_risk(f"u-{i}", "Ventas") for i in range(6)]
    engine = _engine(tmp_path, risk, [_pay(fernet, f"u-{i}", "1000") for i in range(6)])

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert result["row_count"] == 0
    assert (result["status"], result["degraded"]) == ("invalid_key", True)
    assert "status=invalid_key" in caplog.text
    assert bad_key not in caplog.text
    assert bad_key not in json.dumps(engine.published, default=str)


def test_wrong_key_counts_every_token_as_undecryptable(tmp_path, key, caplog):
    caplog.set_level(logging.INFO)
    other = Fernet(Fernet.generate_key())
    risk = [_risk(f"u-{i}", "Ventas") for i in range(6)]
    engine = _engine(tmp_path, risk, [_pay(other, f"u-{i}", "1000") for i in range(6)])

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert result["row_count"] == 0
    assert (result["status"], result["degraded"]) == ("no_publishable_groups", True)
    assert "status=no_publishable_groups" in caplog.text
    assert "undecryptable=6" in caplog.text


def test_decrypted_values_and_key_never_reach_logs_sql_or_results(tmp_path, key, caplog):
    caplog.set_level(logging.DEBUG)
    fernet = Fernet(key.encode())
    amounts = ["47000.41", "47313.97", "49626.05", "51939.44", "52252.18", "58565.46"]
    risk = [_risk(f"u-{i}", "Ventas") for i in range(6)]
    pays = [_pay(fernet, f"u-{i}", amount) for i, amount in enumerate(amounts)]
    engine = _engine(tmp_path, risk, pays)

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    statements = "\n".join(engine._local.statements)
    surfaces = (caplog.text, statements, json.dumps(result), json.dumps(engine.published, default=str))
    for surface in surfaces:
        assert key not in surface
        for amount in amounts:
            assert amount not in surface
            assert str(Decimal(amount) * 12) not in surface
    module_values = [v for v in vars(exposure).values() if isinstance(v, (str, bytes))]
    assert all(key not in str(value) for value in module_values)
    assert "status=published" in caplog.text
    assert (result["status"], result["degraded"]) == ("published", False)


def test_aggregation_failure_is_generic_and_hides_values(tmp_path, key, monkeypatch):
    fernet = Fernet(key.encode())
    risk = [_risk(f"u-{i}", "Ventas") for i in range(5)]
    engine = _engine(tmp_path, risk, [_pay(fernet, f"u-{i}", "4242.42") for i in range(5)])

    def leaky(plain):
        raise ValueError(f"cannot parse {plain.decode()}")

    monkeypatch.setattr(exposure, "_parse_amount", leaky)

    with pytest.raises(exposure.ExposureAggregationError) as caught:
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert "4242.42" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_currency_values_are_quoted_literals_not_sql(tmp_path, key):
    fernet = Fernet(key.encode())
    hostile = "mxn'); drop table gold_x; --"
    risk = [_risk(f"u-{i}", "Ventas") for i in range(5)]
    pays = [_pay(fernet, f"u-{i}", "10", currency=hostile) for i in range(5)]
    engine = _engine(tmp_path, risk, pays)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert row["currency"] == hostile.upper()


def test_groups_span_the_workspace_and_never_publish_departments(tmp_path, key):
    fernet = Fernet(key.encode())
    risk = [_risk(f"a-{i}", "Unidad secreta A") for i in range(3)]
    risk += [_risk(f"b-{i}", "Unidad secreta B") for i in range(3)]
    pays = [_pay(fernet, uid, "3000") for uid in [f"a-{i}" for i in range(3)] + [f"b-{i}" for i in range(3)]]
    engine = _engine(tmp_path, risk, pays)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert (row["risk_band"], row["currency"], row["headcount"]) == ("high", "MXN", 6)
    assert "department_name" not in engine.published["columns"]
    published = json.dumps(engine.published, default=str)
    assert "Unidad secreta" not in published
    reads = [s for s in engine._local.statements if "token_census" in s]
    assert reads and all("department_name" not in s for s in reads)


def test_read_queries_quote_server_resolved_uris():
    engine = DuckDBEngine()
    uri = "s3://lakehouse/silver/sap_successfactors/x/tenant_id=t/workspace_id=w/it's.parquet"
    reader = exposure._reader(engine, uri, {"tenant_id": "t", "workspace_id": "w"})
    sql = exposure.recurring_read_sql(
        reader, reader, {"invalid_score_input", "retention_risk_score"}, TODAY, reader
    )

    assert "it''s.parquet" in sql
    assert sqlglot.parse_one(sql, read="duckdb") is not None
    assert "?" not in sql


def test_out_of_scope_input_is_rejected_before_reading(tmp_path, key):
    engine = _engine(tmp_path, [], [])
    engine._uris[exposure.RISK_SOURCE] = (
        "s3://lakehouse/gold/sap_successfactors/sap_successfactors_talent_retention_risk/"
        "tenant_id=tenant-b/workspace_id=workspace-b/_snapshots/x.parquet"
    )

    with pytest.raises(ValueError, match="outside the caller tenant/workspace scope"):
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert not engine._local.statements


@pytest.mark.parametrize("source", [exposure.RECURRING_SOURCE, exposure.RISK_SOURCE])
def test_unpublished_required_input_is_a_typed_dependency_error(tmp_path, key, source):
    engine = _engine(tmp_path, [_risk("u-0", "Ventas")], [])
    del engine._uris[source]

    with pytest.raises(exposure.ExposureInputError) as caught:
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert caught.value.dependency == source.rsplit("/", 1)[-1]
    assert not [s for s in engine._local.statements if "parquet" in s]


def test_risk_without_validity_flag_fails_closed_to_zero_rows(tmp_path, key):
    fernet = Fernet(key.encode())
    engine = _engine(tmp_path, [], [_pay(fernet, f"u-{i}", "1000") for i in range(6)])
    engine._uris[exposure.RISK_SOURCE] = _write(
        tmp_path / "fallback_risk.parquet",
        RISK_COLUMNS[:3],
        [(f"u-{i}", "Ventas", "high") for i in range(6)],
    )

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert result["row_count"] == 0


def test_stale_recurring_silver_without_end_date_is_rejected(tmp_path, key):
    engine = _engine(tmp_path, [_risk("u-0", "Ventas")], [])
    engine._uris[exposure.RECURRING_SOURCE] = _write(
        tmp_path / "stale.parquet", RECURRING_COLUMNS[:6], []
    )

    with pytest.raises(exposure.ExposureInputError) as caught:
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert caught.value.dependency == "sap_successfactors_emppaycomprecurring_latest"


def test_non_recurring_silver_without_pay_date_is_rejected(tmp_path, key):
    engine = _engine(tmp_path, [_risk("u-0", "Ventas")], [], [])
    engine._uris[exposure.NON_RECURRING_SOURCE] = _write(
        tmp_path / "stale_nr.parquet", NON_RECURRING_COLUMNS[:4], []
    )

    with pytest.raises(exposure.ExposureInputError) as caught:
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert caught.value.dependency == "sap_successfactors_emppaycompnonrecurring_latest"


def test_input_state_tracks_key_without_exposing_it(monkeypatch):
    ds = _dataset()
    assert exposure.exposure_input_state({**ds, "name": "sap_successfactors_talent_9box"}) == []
    assert exposure.exposure_input_state({**ds, "cartridge": "banxico"}) == []

    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    (absent,) = exposure.exposure_input_state(ds)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "short")
    (invalid,) = exposure.exposure_input_state(ds)
    first_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", first_key)
    (first,) = exposure.exposure_input_state(ds)
    (again,) = exposure.exposure_input_state(ds)
    second_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", second_key)
    (second,) = exposure.exposure_input_state(ds)

    assert absent["decryption_key"] == "absent"
    assert set(absent) == {"source", "decryption_key", "rules", "as_of"}
    assert invalid["decryption_key"] == "invalid"
    assert first == again
    assert re.fullmatch(r"key:[0-9a-f]{16}", first["decryption_key"])
    assert first["decryption_key"] != second["decryption_key"]
    for state, value in ((first, first_key), (second, second_key)):
        assert value not in json.dumps(state)
        assert value[:16] not in json.dumps(state)


def test_publication_identity_changes_when_the_key_appears(monkeypatch):
    ds = {**_dataset(), "sources": []}
    ctx = {
        "tenant_id": "00000000-0000-4000-8000-000000000001",
        "workspace_id": "00000000-0000-4000-8000-000000000002",
    }
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    without = PublicationIdentity.build(ds, ctx, input_state=resolve_input_state(None, ds, ctx))
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    state = resolve_input_state(None, ds, ctx)
    with_key = PublicationIdentity.build(ds, ctx, input_state=state)

    assert state[-1]["source"].startswith("materializer/sap_successfactors/")
    assert without.input_digest != with_key.input_digest
    other = {**ds, "name": "sap_successfactors_talent_9box"}
    assert resolve_input_state(None, other, ctx) == []


def test_duckdb_engine_routes_only_the_exposure_dataset(monkeypatch):
    called = {}

    def fake(engine, ds, user_context):
        called["name"] = ds["name"]
        return {"name": ds["name"], "layer": "gold", "row_count": 0, "storage_uri": ""}

    monkeypatch.setattr("app.successfactors_exposure_materializer.materialize_exposure_dataset", fake, raising=False)
    monkeypatch.setattr(exposure, "materialize_exposure_dataset", fake)
    engine = DuckDBEngine()

    result = engine.materialize({**_dataset(), "sql_def": "SELECT should_not_run"}, CTX)

    assert result["row_count"] == 0
    assert called == {"name": exposure.EXPOSURE_DATASET}

    def refuse(*_args, **_kwargs):
        raise RuntimeError("sql path")

    monkeypatch.setattr(engine, "_validate_safe_sql", refuse)
    with pytest.raises(RuntimeError, match="sql path"):
        engine.materialize(
            {**_dataset(), "name": "sap_successfactors_talent_9box", "sql_def": "SELECT 1"},
            CTX,
        )


def test_contract_sql_matches_materializer_output_and_inputs():
    text = CONTRACT_SQL.read_text(encoding="utf-8")
    con = _duckdb().connect()
    try:
        declared = [item[0] for item in con.execute(text).description]
        produced = [item[0] for item in con.execute(exposure.publication_sql([])).description]
        declared_types = [row[1] for row in con.execute(f"DESCRIBE {text.split(chr(10), 3)[3]}").fetchall()]
        produced_types = [row[1] for row in con.execute(f"DESCRIBE {exposure.publication_sql([])}").fetchall()]
    finally:
        con.close()
    assert declared == produced == CONTRACT_COLUMNS
    assert declared_types == produced_types
    sources = json.loads(re.match(r"^--\s+sources:\s+(\[.*\])\s*$", text.splitlines()[1]).group(1))
    assert tuple(sources) == exposure.EXPOSURE_SOURCES
    assert "managed_by_" in text and "_materializer" in text


def test_annualization_map_is_explicit():
    assert exposure.FREQUENCY_FACTORS == {
        "ANN": 1, "ANNUAL": 1, "MON": 12, "MONTHLY": 12, "BWK": 26, "BIWEEKLY": 26,
        "SMO": 24, "SEMIMONTHLY": 24, "WKL": 52, "WEEKLY": 52, "QTR": 4, "QUARTERLY": 4,
    }
    assert exposure.MIN_GROUP_SIZE == 5


def _publish(tmp_path, name, risk, pays):
    (tmp_path / name).mkdir()
    engine = _engine(tmp_path / name, risk, pays)
    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)
    return _by_group(engine.published["rows"])


def _assert_difference_never_resolves(before, after, salary):
    unit = max(
        exposure.rounding_unit(before["exact"], before["largest"]),
        exposure.rounding_unit(after["exact"], after["largest"]),
    )
    assert unit >= salary
    difference = before["row"]["annualized_comp_total"] - after["row"]["annualized_comp_total"]
    assert difference % unit == 0
    assert difference != salary
    bracket = (
        exposure.rounding_unit(before["exact"], before["largest"])
        + exposure.rounding_unit(after["exact"], after["largest"])
    ) / 2
    assert bracket >= salary


def test_band_move_difference_is_a_multiple_of_a_unit_above_the_salary(tmp_path, key):
    fernet = Fernet(key.encode())
    salaries = {f"u-{i}": Decimal(40000 + 1111 * i) for i in range(7)}
    pays = [_pay(fernet, uid, str(v), frequency="ANN") for uid, v in salaries.items()]
    day1 = [_risk(uid, "Ventas", "high") for uid in salaries]
    day2 = [_risk(uid, "Ventas", "medium" if uid == "u-6" else "high") for uid in salaries]

    before = _publish(tmp_path, "d1", day1, pays)[("high", "MXN")]
    after_groups = _publish(tmp_path, "d2", day2, pays)

    after = after_groups[("high", "MXN")]
    assert ("medium", "MXN") not in after_groups
    assert (before["headcount"], after["headcount"]) == (7, 6)
    exact_before = sum(salaries.values())
    exact_after = exact_before - salaries["u-6"]
    _assert_difference_never_resolves(
        {"row": before, "exact": exact_before, "largest": salaries["u-6"]},
        {"row": after, "exact": exact_after, "largest": salaries["u-5"]},
        salaries["u-6"],
    )
    assert before["annualized_comp_total"] == after["annualized_comp_total"] == Decimal("300000.00")
    _assert_avg_derives_from_published_total([before, after])


def test_small_group_near_a_power_of_ten_hides_a_departure(tmp_path, key):
    fernet = Fernet(key.encode())
    salaries = {f"s-{i}": Decimal(19996 + i) for i in range(5)}
    salaries["leaver"] = Decimal("20000")
    pays = [_pay(fernet, uid, str(v), frequency="ANN") for uid, v in salaries.items()]
    everyone = [_risk(uid, "Planta") for uid in salaries]
    remaining = [_risk(uid, "Planta", "low" if uid == "leaver" else "high") for uid in salaries]

    before = _publish(tmp_path, "d1", everyone, pays)[("high", "MXN")]
    after = _publish(tmp_path, "d2", remaining, pays)[("high", "MXN")]

    assert (before["headcount"], after["headcount"]) == (6, 5)
    exact_after = sum(v for uid, v in salaries.items() if uid != "leaver")
    assert exact_after == Decimal("99990")
    assert after["annualized_comp_total"] == Decimal("100000.00")
    assert after["annualized_comp_avg"] == Decimal("20000.00")
    assert exposure.round_significant(exact_after + salaries["leaver"]) == Decimal("120000.00")
    _assert_difference_never_resolves(
        {"row": before, "exact": exact_after + salaries["leaver"], "largest": Decimal("20000")},
        {"row": after, "exact": exact_after, "largest": Decimal("20000")},
        salaries["leaver"],
    )
    assert before["annualized_comp_total"] - after["annualized_comp_total"] == 0


def test_large_groups_keep_about_two_significant_figures():
    fernet = Fernet(Fernet.generate_key())
    amounts = [Decimal(30000 + 100 * i) for i in range(300)]
    records = [
        ("high", "MXN", f"u-{i}", "ANN", fernet.encrypt(str(v).encode()).decode(), 1)
        for i, v in enumerate(amounts)
    ]

    outcome = exposure.aggregate_exposure(iter(records), {}, fernet)

    (row,) = outcome.rows
    exact = sum(amounts)
    assert exact == Decimal("13485000")
    assert row["annualized_comp_total"] == exposure.round_significant(exact) == Decimal("13000000.00")
    assert abs(row["annualized_comp_total"] - exact) / exact < Decimal("0.05")
    assert row["annualized_comp_avg"] == Decimal("43000.00")
    _assert_avg_derives_from_published_total(outcome.rows)


def test_group_whose_total_rounds_to_zero_is_suppressed_not_published_as_zero():
    fernet = Fernet(Fernet.generate_key())
    values = ["8500", "8500", "8500", "8500", "11000"]
    records = [
        ("high", "MXN", f"u-{i}", "ANN", fernet.encrypt(v.encode()).decode(), 1)
        for i, v in enumerate(values)
    ]

    outcome = exposure.aggregate_exposure(iter(records), {}, fernet)

    assert outcome.rows == []
    assert outcome.counters["suppressed_below_unit_groups"] == 1


@pytest.mark.parametrize(
    "total, largest, unit",
    [
        ("303331", "46666", "100000"),
        ("99990", "20000", "100000"),
        ("13485000", "59900", "1000000"),
        ("800", "400", "1000"),
        ("250000", "100000", "100000"),
        ("250000", "100000.01", "1000000"),
        ("5000", "1000", "1000"),
    ],
)
def test_rounding_unit_is_never_finer_than_the_largest_amount(total, largest, unit):
    computed = exposure.rounding_unit(Decimal(total), Decimal(largest))
    assert computed == Decimal(unit)
    assert computed >= Decimal(largest)
    assert computed >= exposure.round_significant(Decimal(total)) / 100


def test_rounding_to_the_unit_rounds_half_up():
    assert exposure.round_to_unit(Decimal("350003.5"), Decimal("100000")) == Decimal("400000.00")
    assert exposure.round_to_unit(Decimal("349999.99"), Decimal("100000")) == Decimal("300000.00")
    assert exposure.round_to_unit(Decimal("45000"), Decimal("100000")) == Decimal("0.00")


def test_group_dominated_by_one_member_is_suppressed(tmp_path, key, caplog):
    caplog.set_level(logging.INFO)
    fernet = Fernet(key.encode())
    amounts = {f"d-{i}": "10000" for i in range(4)}
    amounts["d-4"] = "2000000"
    risk = [_risk(uid, "Direccion") for uid in amounts]
    pays = [_pay(fernet, uid, value, frequency="ANN") for uid, value in amounts.items()]
    engine = _engine(tmp_path, risk, pays)

    result = exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert result["row_count"] == 0
    assert (result["status"], result["degraded"]) == ("no_publishable_groups", True)
    assert engine.published["rows"] == []
    assert "suppressed_dominance_groups=1" in caplog.text
    assert "2000000" not in caplog.text and "2040000" not in caplog.text


@pytest.mark.parametrize("top, published", [("400", True), ("401", False)])
def test_dominance_limit_is_strictly_above_half_the_total(top, published):
    fernet = Fernet(Fernet.generate_key())
    values = ["100", "100", "100", "100", top]
    records = [
        ("low", "MXN", f"u-{i}", "ANN", fernet.encrypt(v.encode()).decode(), 1)
        for i, v in enumerate(values)
    ]

    outcome = exposure.aggregate_exposure(iter(records), {}, fernet)

    assert bool(outcome.rows) is published
    assert outcome.counters["suppressed_dominance_groups"] == (0 if published else 1)


def test_replayed_ciphertext_never_publishes_the_copied_amount():
    fernet = Fernet(Fernet.generate_key())
    target = fernet.encrypt(b"123456.78").decode()
    records = [("high", "MXN", f"member-{i}", "ANN", target, 5) for i in range(5)]

    outcome = exposure.aggregate_exposure(iter(records), {}, fernet)

    assert outcome.status == exposure.STATUS_NO_GROUPS
    assert outcome.rows == []
    assert outcome.counters["duplicate_ciphertext"] == 5
    assert outcome.counters["undecryptable"] == 0


def test_copied_ciphertext_is_excluded_wherever_it_appears(tmp_path, key, caplog):
    caplog.set_level(logging.INFO)
    fernet = Fernet(key.encode())
    victim = fernet.encrypt(b"123456.78").decode()
    risk = [_risk("victim", "Ventas")] + [_risk(f"m-{i}", "Ventas") for i in range(5)]
    risk += [_risk(f"p-{i}", "Planta", "medium") for i in range(6)]
    pays = [_pay(fernet, "victim", "", frequency="ANN", token=victim)]
    pays += [_pay(fernet, f"m-{i}", "", frequency="ANN", token=victim) for i in range(5)]
    pays += [_pay(fernet, f"p-{i}", "1000", frequency="ANN") for i in range(6)]
    copied_bonus = pays[-1][2]
    pays.append(
        _pay(fernet, "p-0", "", frequency="ANN", component="OLD", token=victim,
             start=TODAY - timedelta(days=400), end=TODAY - timedelta(days=100))
    )
    bonuses = [("p-1", "BONUS", copied_bonus, "MXN", TODAY - timedelta(days=5))]
    engine = _engine(tmp_path, risk, pays, bonuses)

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert (row["risk_band"], row["headcount"]) == ("medium", 5)
    assert row["annualized_comp_total"] == Decimal("5000.00")
    assert "duplicate_ciphertext=7" in caplog.text
    published = json.dumps(
        [{key: value for key, value in item.items() if key != "generated_at"} for item in engine.published["rows"]],
        default=str,
    )
    assert "123456" not in published


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2345678", "2300000.00"),
        ("469135", "470000.00"),
        ("303331", "300000.00"),
        ("256665", "260000.00"),
        ("125000", "130000.00"),
        ("999999", "1000000.00"),
        ("12.345", "12.00"),
        ("0", "0.00"),
    ],
)
def test_amounts_are_rounded_to_two_significant_figures(value, expected):
    assert format(exposure.round_significant(Decimal(value)), "f") == expected


def _materializer_copies():
    copies = [exposure]
    try:
        copies.append(importlib.import_module("app.successfactors_exposure_materializer"))
    except ModuleNotFoundError:
        pass
    return copies


def _patch_today(monkeypatch, day: date) -> None:
    for module in _materializer_copies():
        monkeypatch.setattr(module, "_utc_today", lambda: day)


def test_evaluation_date_is_part_of_the_publication_identity(monkeypatch):
    ds = {**_dataset(), "sources": []}
    ctx = {
        "tenant_id": "00000000-0000-4000-8000-000000000001",
        "workspace_id": "00000000-0000-4000-8000-000000000002",
    }
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())

    _patch_today(monkeypatch, date(2026, 9, 29))
    first = PublicationIdentity.build(ds, ctx, input_state=resolve_input_state(None, ds, ctx))
    again = PublicationIdentity.build(ds, ctx, input_state=resolve_input_state(None, ds, ctx))
    _patch_today(monkeypatch, date(2026, 9, 30))
    state = resolve_input_state(None, ds, ctx)
    later = PublicationIdentity.build(ds, ctx, input_state=state)

    assert first.input_digest == again.input_digest
    assert later.input_digest != first.input_digest
    assert state[-1]["as_of"] == "2026-09-30"


@pytest.mark.parametrize(
    "as_of, expected", [("2026-06-15", Decimal("100000.00")), ("2026-08-01", Decimal("1000000.00"))]
)
def test_bound_evaluation_date_selects_the_effective_components(tmp_path, key, as_of, expected):
    fernet = Fernet(key.encode())
    risk = [_risk(f"u-{i}", "Legal") for i in range(5)]
    pays = [
        _pay(fernet, f"u-{i}", "1000", start=date(2026, 1, 1), end=date(2026, 6, 30))
        for i in range(5)
    ]
    pays += [
        _pay(fernet, f"u-{i}", "20000", component="BASE2", start=date(2026, 7, 1), end=None)
        for i in range(5)
    ]
    engine = _engine(tmp_path, risk, pays)
    engine._input_items = lambda: {exposure._INPUT_STATE_SOURCE: {"as_of": as_of}}

    exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    (row,) = engine.published["rows"]
    assert row["annualized_comp_total"] == expected
    reads = [s for s in engine._local.statements if "token_census" in s]
    assert reads and all(f"DATE '{as_of}'" in s for s in reads)
    assert all("CURRENT_DATE" not in s for s in reads)


def test_aggregation_failure_releases_per_person_values(key, monkeypatch):
    fernet = Fernet(key.encode())
    records = [
        ("high", "MXN", f"u-{i}", "ANN",
         fernet.encrypt(str(40000 + 1111 * i).encode()).decode(), 1)
        for i in range(7)
    ]
    real = exposure._parse_amount
    calls: list[int] = []

    def flaky(plain):
        calls.append(1)
        if len(calls) == 6:
            raise RuntimeError("boom")
        return real(plain)

    monkeypatch.setattr(exposure, "_parse_amount", flaky)

    with pytest.raises(RuntimeError) as caught:
        exposure.aggregate_exposure(iter(records), {}, fernet)

    frames = []
    tb = caught.value.__traceback__
    while tb is not None:
        frames.append(tb.tb_frame.f_locals)
        tb = tb.tb_next
    aggregate_frames = [frame for frame in frames if "totals" in frame]
    assert aggregate_frames
    for frame in aggregate_frames:
        assert frame["totals"] == {}
        for name in ("plain", "value", "people", "positive", "total"):
            assert frame[name] is None


def test_materialization_failure_keeps_no_reachable_salary(tmp_path, key, monkeypatch):
    fernet = Fernet(key.encode())
    salaries = [str(40000 + 1111 * i) for i in range(7)]
    risk = [_risk(f"u-{i}", "Ventas") for i in range(7)]
    pays = [_pay(fernet, f"u-{i}", v, frequency="ANN") for i, v in enumerate(salaries)]
    engine = _engine(tmp_path, risk, pays)
    real = exposure._parse_amount

    def leaky(plain):
        value = real(plain)
        if value > Decimal(45000):
            raise RuntimeError("boom")
        return value

    monkeypatch.setattr(exposure, "_parse_amount", leaky)

    with pytest.raises(exposure.ExposureAggregationError) as caught:
        exposure.materialize_exposure_dataset(engine, _dataset(), CTX)

    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    gc.collect()
    inspected = 0
    tb = caught.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == exposure.__file__:
            inspected += 1
            for local in tb.tb_frame.f_locals.values():
                if isinstance(local, (dict, list, tuple, set, str, bytes, Decimal)):
                    text = repr(local) if isinstance(local, bytes) else json.dumps(local, default=str)
                    assert not any(salary in text for salary in salaries)
        tb = tb.tb_next
    assert inspected >= 2


def test_replayed_results_carry_the_status_of_a_fresh_run(monkeypatch):
    ds = _dataset()
    base = {"name": ds["name"], "layer": "gold", "row_count": 0}

    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    assert exposure.with_exposure_status(ds, base) == {
        **base, "status": "missing_key", "degraded": True
    }
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "not-a-fernet-key")
    assert exposure.with_exposure_status(ds, base)["status"] == "invalid_key"
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert exposure.with_exposure_status(ds, base)["status"] == "no_publishable_groups"
    assert exposure.with_exposure_status(ds, base)["degraded"] is True
    assert exposure.with_exposure_status(ds, {**base, "row_count": 3})["degraded"] is False
    fresh = {**base, "status": "missing_key", "degraded": True}
    assert exposure.with_exposure_status(ds, fresh) is fresh
    other = {**base, "name": "sap_successfactors_talent_9box"}
    assert exposure.with_exposure_status({**ds, "name": other["name"]}, other) is other


def _outcome_module():
    spec = importlib.util.spec_from_file_location(
        "exposure_refresh_outcome", REPO_ROOT / "airflow/dags/dataset_refresh_outcome.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _JobError(Exception):
    def __init__(self, status_code: int, detail: dict) -> None:
        super().__init__("job failed")
        self.status_code = status_code
        self.result = {"detail": detail}


def test_exposure_input_error_is_the_typed_publication_input_error():
    error = exposure.ExposureInputError(exposure.RISK_SOURCE)
    assert isinstance(error, exposure.PublicationInputOutdated)
    assert exposure.PublicationInputOutdated.__module__.endswith("publication_snapshot")
    assert error.dependency == "sap_successfactors_talent_retention_risk"
    assert "sap_" not in str(error)


def test_typed_input_error_reaches_the_chain_as_a_named_outdated_dependency(monkeypatch):
    main = importlib.import_module("refinement.app.main")
    monkeypatch.setattr(main, "_log_internal_error", lambda *_a, **_k: "req-exposure")
    outcome = _outcome_module()

    status, detail = main._friendly_duckdb_error(
        main.PublicationInputOutdated(exposure.RECURRING_SOURCE), exposure.EXPOSURE_DATASET
    )

    assert status == 409
    assert detail["code"] == "dependency_not_materialized"
    assert detail["dependency"] == "sap_successfactors_emppaycomprecurring_latest"
    error = _JobError(status, detail)
    assert outcome.missing_source_error_code(error) == "dependency_not_materialized"
    assert outcome.outdated_dependency(error) == "sap_successfactors_emppaycomprecurring_latest"

    text_only = ValueError(str(main.PublicationInputOutdated(exposure.RECURRING_SOURCE)))
    status, detail = main._friendly_duckdb_error(text_only, exposure.EXPOSURE_DATASET)
    assert (status, detail["code"]) == (422, "materialization_failed")
    assert "dependency" not in detail


def test_refinement_wrapper_labels_replayed_and_failing_exposure_runs(monkeypatch):
    main = importlib.import_module("refinement.app.main")
    outcome = _outcome_module()
    ds = _dataset()

    class _Replay:
        def materialize(self, dataset, user_context):
            return {"name": dataset["name"], "layer": "gold", "row_count": 0}

    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(main, "engine", _Replay())
    result = main._materialize_with_operational_fallback(ds, {})
    assert result == {
        "name": ds["name"], "layer": "gold", "row_count": 0,
        "status": "missing_key", "degraded": True,
    }
    classification, payload = outcome.classify_materialization_payload(
        result, expected_name=ds["name"]
    )
    assert classification == outcome.RESULT_DEGRADED
    assert payload == {"name": ds["name"], "layer": "gold", "row_count": 0}

    class _Outdated:
        def materialize(self, dataset, user_context):
            raise main.PublicationInputOutdated(exposure.RECURRING_SOURCE)

    monkeypatch.setattr(main, "engine", _Outdated())
    with pytest.raises(main.PublicationInputOutdated):
        main._materialize_with_operational_fallback(ds, {})


def test_outcome_log_line_survives_the_secret_redaction_filter():
    logging_config = importlib.import_module("refinement.app.logging_config")
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Keep()
    handler.addFilter(logging_config.SecretRedactionFilter())
    previous = exposure.logger.level
    exposure.logger.addHandler(handler)
    exposure.logger.setLevel(logging.INFO)
    try:
        exposure._log_outcome(
            exposure.ExposureOutcome(
                status="published",
                counters=Counter(
                    duplicate_ciphertext=2, suppressed_dominance_groups=1, published_groups=3
                ),
            )
        )
    finally:
        exposure.logger.removeHandler(handler)
        exposure.logger.setLevel(previous)

    (record,) = records
    message = record.getMessage()
    assert "REDACTED" not in message
    assert "duplicate_ciphertext=2" in message
    assert "suppressed_dominance_groups=1" in message
    assert "status=published groups=3" in message

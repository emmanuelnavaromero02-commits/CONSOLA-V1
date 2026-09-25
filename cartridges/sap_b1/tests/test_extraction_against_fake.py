"""The cartridge reads the Business One-shaped fake the way it will read HANA.

Every test here runs ``extraction_service.run_entity`` against a real
Postgres loaded with the deterministic dataset, with the platform side
(run log, watermarks, parquet upload) recorded in memory. Expected values
are computed from the generated rows, never from the SQL under test.
"""
from __future__ import annotations

import importlib
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

b1 = importlib.import_module("sap_b1_fake.schema")

pytestmark = pytest.mark.usefixtures("fake_postgres")

def _read_parquet(path):
    """The file exactly as written. Newer pyarrow infers partition columns
    (load_date, batch_id, ...) from the hive-style directories when a single
    file path goes through ``read_table``."""
    import pyarrow.parquet as pq

    return pq.ParquetFile(str(path)).read()



class Recorder:
    def __init__(self, watermarks: dict[str, str] | None = None) -> None:
        self.runs: list[dict] = []
        self.finished: list[dict] = []
        self.failed: list[dict] = []
        self.watermarks: dict[str, str] = dict(watermarks or {})
        self.uploads: list[dict] = []

    @property
    def rows(self) -> list[dict]:
        return [row for upload in self.uploads for row in upload["rows"]]


def _install(monkeypatch, watermarks: dict[str, str] | None = None) -> tuple[Recorder, object]:
    from app.services import extraction_service as es

    recorder = Recorder(watermarks)

    def _create_run(**kw):
        recorder.runs.append(kw)
        return "run-1"

    def _upload(**kw):
        recorder.uploads.append(kw)
        return f"s3://lakehouse/raw/sap_b1/{kw['entity']}/{kw['run_id']}.parquet"

    monkeypatch.setattr(es, "create_run", _create_run)
    monkeypatch.setattr(es, "finish_run", lambda **kw: recorder.finished.append(kw))
    monkeypatch.setattr(es, "fail_run", lambda **kw: recorder.failed.append(kw))
    monkeypatch.setattr(es, "get_watermark", lambda key: recorder.watermarks.get(key))
    monkeypatch.setattr(
        es, "update_watermark",
        lambda **kw: recorder.watermarks.__setitem__(kw["entity_name"], kw["last_watermark_value"]),
    )
    monkeypatch.setattr(es, "write_parquet_and_upload", _upload)
    return recorder, es


def _config(entity: str, **overrides) -> dict:
    from app.services.catalog_service import _yaml_entities

    config = next(dict(e) for e in _yaml_entities() if e["entity"] == entity)
    config.update(overrides)
    return config


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


def _stamp(table: str, row: tuple) -> datetime:
    day = row[_col(table, "UpdateDate")]
    ts = row[_col(table, "UpdateTS")]
    return datetime(day.year, day.month, day.day, ts // 10000, ts // 100 % 100, ts % 100)


def _fmt(stamp: datetime) -> str:
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")


def _lift_clock_cap(monkeypatch) -> None:
    """The fake's newest stamps lie after today's date, so the source clock
    would cap the recorded watermark below them. Tests that assert the exact
    max stamp lift the cap; the cap has its own test."""
    from app.core import b1_source

    monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: datetime(2099, 1, 1))


# ── whole-table reads ──────────────────────────────────────────────────────


def test_full_load_reads_every_company(b1_env, dataset, monkeypatch):
    _lift_clock_cap(monkeypatch)
    recorder, es = _install(monkeypatch)
    result = es.run_entity(_config("OINV", mode="full"))

    expected = {c.alias: len(dataset.tables[c.alias]["OINV"]) for c in dataset.companies}
    assert result["status"] == "success" and result["mode"] == "full"
    assert result["record_count"] == sum(expected.values()) > 0
    assert {alias: info["record_count"] for alias, info in result["companies"].items()} == expected
    by_company = defaultdict(int)
    for row in recorder.rows:
        by_company[row["_company"]] += 1
        assert row["_source_updated_at"]
    assert dict(by_company) == expected
    assert recorder.finished[0]["status"] == "success" and recorder.finished[0]["records_extracted"] == result["record_count"]
    assert recorder.runs[0]["run_type"] == "full" and recorder.runs[0]["entity_name"] == "OINV"
    assert not recorder.failed
    # A full load records the watermark so the next incremental cycle continues from it.
    for company in dataset.companies:
        latest = max(_stamp("OINV", row) for row in dataset.tables[company.alias]["OINV"])
        assert recorder.watermarks[f"OINV@{company.alias}"] == _fmt(latest)
    assert set(recorder.uploads[0]["expected_columns"]) == set(b1.columns("OINV")) | {"_company", "_source_updated_at"}


def test_snapshot_tables_fall_back_to_full_when_asked_for_incremental(b1_env, dataset, monkeypatch):
    recorder, es = _install(monkeypatch)
    result = es.run_entity(_config("OITW", mode="incremental"))
    assert result["mode"] == "full"
    assert result["record_count"] == sum(len(dataset.tables[c.alias]["OITW"]) for c in dataset.companies) > 0
    assert all(row["_source_updated_at"] is None for row in recorder.rows)
    assert recorder.watermarks == {}


def test_the_first_incremental_read_of_an_empty_table_leaves_a_zero_row_artifact(b1_env, dataset, monkeypatch):
    """A/P credit memos exist in no company of the fake. The first
    incremental cycle is a whole-table read, so it must leave the typed
    empty file every silver of that table expects; the second cycle, with
    watermarks or not, leaves nothing."""
    assert not any(dataset.tables[c.alias]["RPC1"] for c in dataset.companies)
    recorder, es = _install(monkeypatch)
    first = es.run_entity(_config("RPC1", mode="incremental"))
    assert first["mode"] == "incremental" and first["record_count"] == 0
    assert len(recorder.uploads) == 1 and recorder.uploads[0]["rows"] == []
    assert recorder.uploads[0]["arrow_schema"] is not None
    assert recorder.watermarks == {}, "nothing was read, so no watermark"
    marks = {f"RPC1@{c.alias}": "2020-01-01T00:00:00" for c in dataset.companies}
    again, es = _install(monkeypatch, marks)
    assert es.run_entity(_config("RPC1", mode="incremental"))["record_count"] == 0
    assert again.uploads == []


def test_a_full_load_that_finds_nothing_leaves_a_zero_row_artifact(b1_env, dataset, monkeypatch):
    distributor = next(c for c in dataset.companies if c.alias != "mx_mfg")
    assert not dataset.tables[distributor.alias]["OWOR"]
    monkeypatch.setenv("SAP_B1_COMPANIES", f"{distributor.alias}={distributor.schema}")
    recorder, es = _install(monkeypatch)
    result = es.run_entity(_config("OWOR", mode="full"))
    assert result["record_count"] == 0 and result["status"] == "success"
    assert len(recorder.uploads) == 1 and recorder.uploads[0]["rows"] == []
    assert recorder.watermarks == {}


# ── incremental reads ──────────────────────────────────────────────────────


def _expected_headers(dataset, table: str, since: datetime) -> dict[str, set]:
    boundary = since - timedelta(minutes=5)
    return {
        c.alias: {row[_col(table, "DocEntry")] for row in dataset.tables[c.alias][table] if _stamp(table, row) >= boundary}
        for c in dataset.companies
    }


def test_incremental_follows_the_update_stamp_rule_per_company(b1_env, dataset, monkeypatch):
    _lift_clock_cap(monkeypatch)
    since = datetime.combine(dataset.as_of - timedelta(days=45), datetime.min.time()).replace(hour=12)
    marks = {f"OINV@{c.alias}": _fmt(since) for c in dataset.companies}
    recorder, es = _install(monkeypatch, marks)
    result = es.run_entity(_config("OINV", mode="incremental"))

    expected = _expected_headers(dataset, "OINV", since)
    assert all(expected.values()), "the boundary must leave rows on both sides"
    seen = defaultdict(set)
    for row in recorder.rows:
        seen[row["_company"]].add(row["DocEntry"])
    assert dict(seen) == expected
    assert result["record_count"] == sum(len(v) for v in expected.values())
    for company in dataset.companies:
        info = result["companies"][company.alias]
        assert info["watermark_used"] == _fmt(since)
        latest = max(_stamp("OINV", row) for row in dataset.tables[company.alias]["OINV"])
        assert info["watermark_updated_to"] == _fmt(latest)
        assert recorder.watermarks[f"OINV@{company.alias}"] == _fmt(latest)
    assert recorder.runs[0]["run_type"] == "incremental"


def test_lines_follow_their_header_and_inherit_its_stamp(b1_env, dataset, monkeypatch):
    _lift_clock_cap(monkeypatch)
    since = datetime.combine(dataset.as_of - timedelta(days=45), datetime.min.time()).replace(hour=12)
    marks = {f"INV1@{c.alias}": _fmt(since) for c in dataset.companies}
    recorder, es = _install(monkeypatch, marks)
    es.run_entity(_config("INV1", mode="incremental"))

    headers = _expected_headers(dataset, "OINV", since)
    expected = {
        c.alias: {
            (row[_col("INV1", "DocEntry")], row[_col("INV1", "LineNum")])
            for row in dataset.tables[c.alias]["INV1"]
            if row[_col("INV1", "DocEntry")] in headers[c.alias]
        }
        for c in dataset.companies
    }
    seen = defaultdict(set)
    stamps = {
        (c.alias, row[_col("OINV", "DocEntry")]): _fmt(_stamp("OINV", row))
        for c in dataset.companies
        for row in dataset.tables[c.alias]["OINV"]
    }
    for row in recorder.rows:
        seen[row["_company"]].add((row["DocEntry"], row["LineNum"]))
        assert row["_source_updated_at"] == stamps[(row["_company"], row["DocEntry"])]
    assert dict(seen) == expected
    for company in dataset.companies:
        latest = max(_stamp("OINV", row) for row in dataset.tables[company.alias]["OINV"])
        assert recorder.watermarks[f"INV1@{company.alias}"] == _fmt(latest)


def test_an_incremental_cycle_without_changes_writes_nothing(b1_env, dataset, monkeypatch):
    marks = {}
    for company in dataset.companies:
        latest = max(_stamp("OINV", row) for row in dataset.tables[company.alias]["OINV"])
        marks[f"OINV@{company.alias}"] = _fmt(latest + timedelta(minutes=6))
    recorder, es = _install(monkeypatch, dict(marks))
    result = es.run_entity(_config("OINV", mode="incremental"))
    assert result["record_count"] == 0 and result["status"] == "success"
    assert recorder.uploads == []
    assert result["storage_uri"] == ""
    assert recorder.finished[0]["records_extracted"] == 0
    assert recorder.watermarks == marks
    assert not recorder.failed


def test_journal_lines_read_through_their_entry(b1_env, dataset, monkeypatch):
    since = datetime.combine(dataset.as_of - timedelta(days=20), datetime.min.time())
    marks = {f"JDT1@{c.alias}": _fmt(since) for c in dataset.companies}
    recorder, es = _install(monkeypatch, marks)
    es.run_entity(_config("JDT1", mode="incremental"))
    boundary = since - timedelta(minutes=5)
    for company in dataset.companies:
        entries = {row[_col("OJDT", "TransId")] for row in dataset.tables[company.alias]["OJDT"] if _stamp("OJDT", row) >= boundary}
        expected = {
            (row[_col("JDT1", "TransId")], row[_col("JDT1", "Line_ID")])
            for row in dataset.tables[company.alias]["JDT1"]
            if row[_col("JDT1", "TransId")] in entries
        }
        seen = {(row["TransId"], row["Line_ID"]) for row in recorder.rows if row["_company"] == company.alias}
        assert seen == expected
        assert expected, f"{company.alias}: boundary left no journal lines"
        debit = sum((row["Debit"] - row["Credit"] for row in recorder.rows if row["_company"] == company.alias), Decimal(0))
        assert debit == 0, "lines of a complete entry must balance"


def test_integer_watermark_on_inventory_movements(b1_env, dataset, monkeypatch):
    marks = {}
    for company in dataset.companies:
        numbers = sorted(row[_col("OINM", "TransNum")] for row in dataset.tables[company.alias]["OINM"])
        marks[f"OINM@{company.alias}"] = str(numbers[len(numbers) // 2])
    recorder, es = _install(monkeypatch, dict(marks))
    result = es.run_entity(_config("OINM", mode="incremental"))
    for company in dataset.companies:
        boundary = int(marks[f"OINM@{company.alias}"])
        rows = dataset.tables[company.alias]["OINM"]
        expected = {row[_col("OINM", "TransNum")] for row in rows if row[_col("OINM", "TransNum")] > boundary}
        seen = {row["TransNum"] for row in recorder.rows if row["_company"] == company.alias}
        assert seen == expected and expected
        assert recorder.watermarks[f"OINM@{company.alias}"] == f"{max(r[_col('OINM', 'TransNum')] for r in rows):015d}"
    assert result["record_count"] == len(recorder.rows)


# ── paging, history, failures ──────────────────────────────────────────────


@pytest.mark.parametrize("entity,key", [("OINV", ("DocEntry",)), ("INV1", ("DocEntry", "LineNum")), ("OINM", ("TransNum", "TransSeq"))])
def test_keyset_paging_is_complete_and_free_of_duplicates(b1_env, dataset, monkeypatch, entity, key):
    _lift_clock_cap(monkeypatch)
    small_recorder, es = _install(monkeypatch)
    es.run_entity(_config(entity, mode="full", page_size=7))
    small = sorted((row["_company"], *(row[k] for k in key)) for row in small_recorder.rows)

    big_recorder, es = _install(monkeypatch)
    es.run_entity(_config(entity, mode="full", page_size=100_000))
    big = sorted((row["_company"], *(row[k] for k in key)) for row in big_recorder.rows)

    assert small == big
    assert len(small) == len(set(small)) == sum(len(dataset.tables[c.alias][entity]) for c in dataset.companies)
    if entity == "OINM":
        return
    # A full load of a line table records the header stamp as its watermark.
    for company in dataset.companies:
        latest = max(_stamp("OINV", row) for row in dataset.tables[company.alias]["OINV"])
        assert big_recorder.watermarks[f"{entity}@{company.alias}"] == _fmt(latest)


def test_batch_transactions_are_incremental_by_log_entry(b1_env, dataset, monkeypatch):
    marks = {}
    for company in dataset.companies:
        entries = sorted(row[_col("IBT1", "LogEntry")] for row in dataset.tables[company.alias]["IBT1"])
        marks[f"IBT1@{company.alias}"] = str(entries[len(entries) // 2])
    recorder, es = _install(monkeypatch, dict(marks))
    result = es.run_entity(_config("IBT1", mode="incremental"))
    assert result["mode"] == "incremental"
    for company in dataset.companies:
        boundary = int(marks[f"IBT1@{company.alias}"])
        rows = dataset.tables[company.alias]["IBT1"]
        expected = {row[_col("IBT1", "LogEntry")] for row in rows if row[_col("IBT1", "LogEntry")] > boundary}
        seen = {row["LogEntry"] for row in recorder.rows if row["_company"] == company.alias}
        assert seen == expected and expected
        assert recorder.watermarks[f"IBT1@{company.alias}"] == f"{max(r[_col('IBT1', 'LogEntry')] for r in rows):015d}"


def test_watermark_never_passes_the_source_clock(b1_env, dataset, monkeypatch):
    """A document edited behind the cursor during a long run keeps a stamp
    older than the newest row read; the next cycle must still reach it, so
    the recorded watermark is capped at the clock of the source when the
    run started."""
    from app.core import b1_source

    latest = max(_stamp("OINV", row) for c in dataset.companies for row in dataset.tables[c.alias]["OINV"])
    frozen = latest - timedelta(days=30)
    monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: frozen)
    recorder, es = _install(monkeypatch)
    result = es.run_entity(_config("OINV", mode="full"))
    assert result["record_count"] > 0
    for company in dataset.companies:
        assert recorder.watermarks[f"OINV@{company.alias}"] == _fmt(frozen)
        assert result["companies"][company.alias]["watermark_updated_to"] == _fmt(frozen)


def test_a_failing_company_keeps_the_others_committed(b1_env, dataset, monkeypatch):
    """Companies are flushed and committed one by one: a schema that fails
    never makes the others re-read what they already delivered, and the run
    is still a failure."""
    from app.core.b1_source import B1SourceError, CartridgeCircuitBreaker

    CartridgeCircuitBreaker.reset()
    good = [c for c in dataset.companies][:2]
    monkeypatch.setenv("SAP_B1_COMPANIES", ",".join(f"{c.alias}={c.schema}" for c in good) + ",broken=NO_SUCH_SCHEMA")
    recorder, es = _install(monkeypatch)
    with pytest.raises(B1SourceError):
        es.run_entity(_config("OINV", mode="full"))
    assert len(recorder.failed) == 1 and recorder.finished == []
    assert set(recorder.watermarks) == {f"OINV@{c.alias}" for c in good}
    assert {row["_company"] for row in recorder.rows} == {c.alias for c in good}
    assert "NO_SUCH_SCHEMA" not in recorder.failed[0]["error_message"]
    CartridgeCircuitBreaker.reset()


def test_batches_flush_every_batch_size_rows(b1_env, dataset, monkeypatch):
    recorder, es = _install(monkeypatch)
    monkeypatch.setattr(es, "BATCH_SIZE", 500)
    result = es.run_entity(_config("INV1", mode="full", page_size=300))
    assert result["batches"] == len(recorder.uploads) >= 2
    assert [u["run_id"] for u in recorder.uploads][:2] == ["run-1", "run-1-b1"]
    assert sum(len(u["rows"]) for u in recorder.uploads) == result["record_count"]
    # The buffer is checked after each page lands, so one batch can hold up to
    # BATCH_SIZE - 1 + page_size rows.
    assert max(len(u["rows"]) for u in recorder.uploads) < 500 + 300


def test_historical_range_uses_the_document_date_and_leaves_watermarks_alone(b1_env, dataset, monkeypatch):
    start = (dataset.as_of.replace(day=1) - timedelta(days=1)).replace(day=1)
    end = dataset.as_of.replace(day=1) - timedelta(days=1)
    recorder, es = _install(monkeypatch)
    result = es.run_entity(_config("OINV"), from_date=start.isoformat(), to_date=end.isoformat())
    expected = sum(
        1
        for c in dataset.companies
        for row in dataset.tables[c.alias]["OINV"]
        if start <= row[_col("OINV", "DocDate")].date() <= end
    )
    assert result["mode"] == "historical" and result["record_count"] == expected > 0
    assert all(start <= row["DocDate"].date() <= end for row in recorder.rows)
    assert recorder.watermarks == {}
    assert recorder.runs[0]["run_type"] == "historical"

    line_recorder, es = _install(monkeypatch)
    es.run_entity(_config("INV1"), from_date=start.isoformat(), to_date=end.isoformat())
    header_keys = {(row["_company"], row["DocEntry"]) for row in recorder.rows}
    assert {(row["_company"], row["DocEntry"]) for row in line_recorder.rows} == header_keys


def test_a_connection_failure_is_a_failed_run_not_zero_rows(b1_env, monkeypatch):
    from app.core.b1_source import B1SourceError, CartridgeCircuitBreaker

    CartridgeCircuitBreaker.reset()
    monkeypatch.setenv("SAP_B1_PORT", "1")
    recorder, es = _install(monkeypatch)
    with pytest.raises(B1SourceError):
        es.run_entity(_config("OINV", mode="full"))
    assert len(recorder.failed) == 1
    assert b1_env["password"] not in recorder.failed[0]["error_message"]
    assert recorder.finished == [] and recorder.uploads == []
    CartridgeCircuitBreaker.reset()


def test_circuit_breaker_ignores_bad_passwords_and_recovers(b1_env, monkeypatch):
    from app.core import b1_source

    b1_source.CartridgeCircuitBreaker.reset()
    monkeypatch.setenv("SAP_B1_PASSWORD", "not-the-password")
    for _ in range(4):
        assert b1_source.B1Client().test_connection()["status"] == "auth_error"
    assert b1_source.CartridgeCircuitBreaker.snapshot()["failures"] == 0

    monkeypatch.setenv("SAP_B1_PASSWORD", b1_env["password"])
    monkeypatch.setenv("SAP_B1_PORT", "1")
    for _ in range(3):
        assert b1_source.B1Client().test_connection()["status"] == "error"
    assert b1_source.CartridgeCircuitBreaker.state == "UNHEALTHY"
    assert "circuit breaker" in b1_source.B1Client().test_connection()["message"]

    monkeypatch.setattr(b1_source.CartridgeCircuitBreaker, "cooldown_seconds", 0)
    monkeypatch.setenv("SAP_B1_PORT", str(b1_env["port"]))
    assert b1_source.B1Client().test_connection()["status"] == "ok"
    assert b1_source.CartridgeCircuitBreaker.state == "HEALTHY"
    b1_source.CartridgeCircuitBreaker.reset()


def test_error_messages_never_carry_host_user_or_schema(b1_env, dataset, monkeypatch):
    from app.core.b1_source import B1Client, B1SourceError, CartridgeCircuitBreaker

    CartridgeCircuitBreaker.reset()
    client = B1Client()
    schema = dataset.companies[0].schema
    with pytest.raises(B1SourceError) as failed_query:
        client.fetch_all(f'SELECT "Nope" FROM "{schema}"."OINV"')
    text = str(failed_query.value)
    assert schema not in text and b1_env["host"] not in text and b1_env["password"] not in text
    assert CartridgeCircuitBreaker.snapshot()["failures"] == 0, "a SQL error must not count as a connectivity failure"

    monkeypatch.setenv("SAP_B1_PORT", "1")
    with pytest.raises(B1SourceError) as failed_connect:
        B1Client().fetch_all("SELECT 1")
    text = str(failed_connect.value)
    assert b1_env["host"] not in text and b1_env["user"] not in text and b1_env["password"] not in text
    CartridgeCircuitBreaker.reset()


def test_a_missing_configuration_is_a_failed_run(monkeypatch):
    for name in ("SAP_B1_HOST", "SAP_B1_USER", "SAP_B1_PASSWORD", "SAP_B1_COMPANIES"):
        monkeypatch.delenv(name, raising=False)
    from app.core.b1_source import B1ConfigurationError

    recorder, es = _install(monkeypatch)
    with pytest.raises(B1ConfigurationError, match="SAP_B1_HOST"):
        es.run_entity(_config("OINV", mode="full"))
    assert len(recorder.failed) == 1 and recorder.uploads == []


# ── connectivity and parquet types ─────────────────────────────────────────


def test_test_connection_reports_every_company_without_secrets(b1_env, dataset):
    from app.core.b1_source import B1Client

    info = B1Client().test_connection()
    assert info["status"] == "ok" and info["reachable"] is True
    assert [c["alias"] for c in info["companies"]] == [c.alias for c in dataset.companies]
    assert all(c["reachable"] and isinstance(c["b1_version"], int) for c in info["companies"])
    text = repr(info)
    assert b1_env["password"] not in text and b1_env["host"] not in text and "postgres" != info.get("user")


def test_configuration_status_names_the_missing_variables(monkeypatch):
    for name in ("SAP_B1_HOST", "SAP_B1_USER", "SAP_B1_PASSWORD", "SAP_B1_COMPANIES", "SAP_B1_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SAP_B1_DIALECT", "postgres")
    from app.core.b1_source import B1Client

    status = B1Client().configuration_status()
    assert status["configured"] is False
    assert {"SAP_B1_HOST", "SAP_B1_USER", "SAP_B1_PASSWORD", "SAP_B1_COMPANIES", "SAP_B1_DATABASE"} <= set(status["missing"])
    assert B1Client().test_connection()["status"] == "degraded"


def test_parquet_keeps_exact_amounts_and_dates(b1_env, dataset, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    from app.core.b1_source import B1Client
    from app.services import b1_queries as q
    from app.services import parquet_service

    plan = q.plan_from_config(_config("OINV", page_size=25))
    client = B1Client()
    company = client.companies[0]
    columns, rows = client.fetch_all(*q.select_sql(plan, company.schema, mode="full"))
    records = q.rows_to_records(plan, company.alias, columns, rows)
    assert len(records) == 25

    written: list[Path] = []

    def _copy(*, local_path: str, object_name: str) -> None:
        target = tmp_path / Path(object_name).name
        shutil.copy(local_path, target)
        written.append(target)

    monkeypatch.setattr(parquet_service, "upload_file_to_minio", _copy)
    declared = q.arrow_schema(plan)
    uri = parquet_service.write_parquet_and_upload(
        entity="OINV", rows=records, run_id="run-1", load_type="full",
        watermark_field="UpdateDate", expected_columns=list(plan.output_columns), arrow_schema=declared,
    )
    assert uri.startswith("s3://lakehouse/raw/sap_b1/OINV/")
    table = _read_parquet(written[0])
    assert table.schema.equals(declared)
    schema = {field.name: str(field.type) for field in table.schema}
    assert schema["DocTotal"] == "decimal128(19, 6)" and schema["DocTotalFC"] == "decimal128(19, 6)"
    assert schema["DocDate"] == "timestamp[us]" and schema["UpdateTS"] == "int64"
    assert schema["CardCode"] == "string" and schema["_company"] == "string" and schema["_source_updated_at"] == "string"
    back = table.to_pylist()[0]
    assert back["DocTotal"] == records[0]["DocTotal"]
    assert back["_company"] == company.alias and back["_run_id"] == "run-1"
    assert back["_watermark_value"] == str(records[0]["UpdateDate"])

    # An all-null column and an empty batch keep the declared types, so a
    # DuckDB glob over many files never sees a null-typed column first.
    written.clear()
    nulls = [{**record, "DocTotalFC": None, "Comments": None} for record in records]
    parquet_service.write_parquet_and_upload(entity="OINV", rows=nulls, run_id="run-2", load_type="full", arrow_schema=declared)
    parquet_service.write_parquet_and_upload(entity="OINV", rows=[], run_id="run-3", load_type="full", arrow_schema=declared)
    for path in written:
        assert _read_parquet(path).schema.equals(declared), path
    assert _read_parquet(written[1]).num_rows == 0

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from fake_world import Bronze

b1 = importlib.import_module("sap_b1_fake.schema")

CARTRIDGE_ROOT = Path(__file__).resolve().parents[1]
AGENT = CARTRIDGE_ROOT / "connect" / "windows-agent" / "agent.py"
TENANT = "33333333-3333-4333-8333-333333333333"
WORKSPACE = "44444444-4444-4444-8444-444444444444"
_STAMP = "%Y-%m-%dT%H:%M:%S"
_PLATFORM_PREFIXES = (
    "DATABASE_URL", "MINIO_", "LAKEHOUSE_", "FIELD_ENCRYPTION_KEY", "INTERNAL_API_KEY",
    "SECURITY_CONTEXT", "AWS_", "OMEGA_", "APP_ENV", "S3_BUCKET", "GCS_",
)
PAGED = ("OINV", "INV1", "JDT1", "OINM", "ORTT", "OITW", "OBTQ", "OCRD")


def _catalogue() -> list[dict]:
    from app.services.catalog_service import _yaml_entities

    return [dict(e) for e in _yaml_entities()]


def _config(entity: str, **overrides) -> dict:
    config = next(e for e in _catalogue() if e["entity"] == entity)
    config.update(overrides)
    return config


def _plan(config: dict):
    from app.services import b1_queries as q

    return q.plan_from_config(config)


def _stamp(table: str, row: tuple, date_field: str = "UpdateDate", ts_field: str = "UpdateTS") -> datetime:
    columns = b1.columns(table)
    day = row[columns.index(date_field)]
    ts = int(row[columns.index(ts_field)] or 0)
    return datetime(day.year, day.month, day.day, ts // 10000, ts // 100 % 100, ts % 100)


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


class Recorder:
    def __init__(self, watermarks: dict[str, str] | None = None) -> None:
        self.watermarks: dict[str, str] = dict(watermarks or {})
        self.uploads: list[dict] = []
        self.failed: list[dict] = []

    @property
    def rows(self) -> list[dict]:
        return [row for upload in self.uploads for row in upload["rows"]]


def _install(monkeypatch, watermarks: dict[str, str] | None = None):
    from app.core import b1_source
    from app.services import extraction_service as es

    recorder = Recorder(watermarks)
    monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: datetime(2099, 1, 1))
    monkeypatch.setattr(es, "create_run", lambda **kw: "run-1")
    monkeypatch.setattr(es, "finish_run", lambda **kw: None)
    monkeypatch.setattr(es, "fail_run", lambda **kw: recorder.failed.append(kw))
    monkeypatch.setattr(es, "get_watermark", lambda key: recorder.watermarks.get(key))
    monkeypatch.setattr(
        es, "update_watermark", lambda **kw: recorder.watermarks.__setitem__(kw["entity_name"], kw["last_watermark_value"])
    )
    monkeypatch.setattr(es, "write_parquet_and_upload", lambda **kw: recorder.uploads.append(kw) or "s3://x")
    return recorder, es


def _source_rows(dataset, plan) -> dict[str, list[tuple[dict, datetime | None]]]:
    from app.services import b1_queries as q

    out: dict[str, list[tuple[dict, datetime | None]]] = {}
    for company in dataset.companies:
        tables = dataset.tables[company.alias]
        headers: dict = {}
        if plan.watermark_kind == q.WATERMARK_UPDATE_TS and plan.parent:
            key = b1.columns(plan.parent).index(plan.parent_key)
            headers = {row[key]: _stamp(plan.parent, row, plan.watermark_field, plan.watermark_ts_field) for row in tables[plan.parent]}
        rows = []
        for row in tables[plan.table]:
            record = dict(zip(b1.columns(plan.table), row))
            stamp = None
            if plan.watermark_kind == q.WATERMARK_UPDATE_TS:
                stamp = (
                    headers[record[plan.join_key]]
                    if plan.parent
                    else _stamp(plan.table, row, plan.watermark_field, plan.watermark_ts_field)
                )
            rows.append((record, stamp))
        out[company.alias] = rows
    return out


def _as_tuple(plan, record: dict, alias: str, stamp: datetime | None) -> tuple:
    return (*(record[c] for c in plan.columns), alias, stamp.strftime(_STAMP) if stamp else None)


def _read(plan, rows: list[dict]) -> Counter:
    return Counter(tuple(row[c] for c in plan.output_columns) for row in rows)


def _expected_watermarks(plan, selected: dict[str, list[tuple[dict, datetime | None]]]) -> dict[str, str]:
    from app.services import b1_queries as q

    marks = {}
    for alias, rows in selected.items():
        if not rows or plan.watermark_kind is None:
            continue
        if plan.watermark_kind == q.WATERMARK_UPDATE_TS:
            marks[f"{plan.entity}@{alias}"] = max(stamp for _record, stamp in rows).strftime(_STAMP)
        else:
            marks[f"{plan.entity}@{alias}"] = f"{max(int(r[plan.watermark_field]) for r, _s in rows):015d}"
    return marks


def test_a_full_read_of_every_entity_returns_exactly_the_source_rows(b1_backend, dataset, monkeypatch):
    from app.services import b1_queries as q

    for config in _catalogue():
        plan = _plan(config)
        recorder, es = _install(monkeypatch)
        result = es.run_entity({**config, "mode": "full"})
        source = _source_rows(dataset, plan)
        expected = Counter(_as_tuple(plan, r, alias, s) for alias, rows in source.items() for r, s in rows)
        assert result["status"] == "success" and not recorder.failed, plan.entity
        assert _read(plan, recorder.rows) == expected, f"{b1_backend['dialect']}: {plan.entity} differs from the source"
        assert result["record_count"] == sum(expected.values())
        assert recorder.watermarks == _expected_watermarks(plan, source), plan.entity
        for upload in recorder.uploads:
            assert upload["expected_columns"] == list(plan.output_columns)
            assert upload["arrow_schema"] == q.arrow_schema(plan)


def test_an_incremental_read_of_every_entity_returns_exactly_the_changed_rows(b1_backend, dataset, monkeypatch):
    from app.services import b1_queries as q

    since = datetime.combine(dataset.as_of - timedelta(days=45), datetime.min.time()).replace(hour=12)
    for config in _catalogue():
        plan = _plan(config)
        if not plan.incremental_capable:
            continue
        source = _source_rows(dataset, plan)
        if plan.watermark_kind == q.WATERMARK_UPDATE_TS:
            marks = {f"{plan.entity}@{alias}": since.strftime(_STAMP) for alias in source}
            boundary = since - timedelta(minutes=5)
            selected = {alias: [(r, s) for r, s in rows if s >= boundary] for alias, rows in source.items()}
        else:
            middles = {
                alias: sorted(int(r[plan.watermark_field]) for r, _s in rows)[len(rows) // 2] if rows else 0
                for alias, rows in source.items()
            }
            marks = {f"{plan.entity}@{alias}": str(middles[alias]) for alias in source}
            selected = {
                alias: [(r, s) for r, s in rows if int(r[plan.watermark_field]) > middles[alias]]
                for alias, rows in source.items()
            }
        recorder, es = _install(monkeypatch, marks)
        result = es.run_entity({**config, "mode": "incremental"})
        expected = Counter(_as_tuple(plan, r, alias, s) for alias, rows in selected.items() for r, s in rows)
        assert result["mode"] == "incremental" and not recorder.failed
        assert _read(plan, recorder.rows) == expected, f"{b1_backend['dialect']}: {plan.entity}"
        advanced = dict(marks)
        for key, value in _expected_watermarks(plan, selected).items():
            if q.Watermark.parse(plan.watermark_kind, marks[key]) < q.Watermark.parse(plan.watermark_kind, value):
                advanced[key] = value
        assert recorder.watermarks == advanced, plan.entity


@pytest.mark.parametrize("entity", PAGED)
def test_pages_of_seven_rows_cover_every_row_exactly_once(b1_backend, dataset, monkeypatch, entity):
    plan = _plan(_config(entity))
    recorder, es = _install(monkeypatch)
    es.run_entity(_config(entity, mode="full", page_size=7))
    got = [tuple(row[c] for c in plan.output_columns) for row in recorder.rows]
    source = _source_rows(dataset, plan)
    expected = Counter(_as_tuple(plan, r, alias, s) for alias, rows in source.items() for r, s in rows)
    keys = [(row["_company"], *(row[k] for k in plan.primary_key)) for row in recorder.rows]
    assert len(keys) == len(set(keys)), f"{entity}: a page repeated a row"
    assert Counter(got) == expected and len(got) > 7 * 3
    assert recorder.watermarks == _expected_watermarks(plan, source)


def test_bronze_files_are_identical_on_every_engine(fake_postgres, fake_mssql, switch_backend, dataset, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    from app.services import b1_queries as q
    from app.services import extraction_service as es

    produced: dict[str, dict[str, object]] = {}
    marks: dict[str, dict[str, str]] = {}
    for fake in (fake_postgres, fake_mssql):
        switch_backend(fake)
        root = tmp_path / fake["dialect"]
        bronze = Bronze(root, today=date(2026, 9, 25))
        bronze.install(monkeypatch)
        for config in _catalogue():
            es.run_entity({**config, "mode": "full"})
        assert not bronze.failed
        marks[fake["dialect"]] = dict(bronze.watermarks)
        produced[fake["dialect"]] = {
            path.relative_to(root).as_posix(): pq.ParquetFile(path) for path in sorted(root.rglob("*.parquet"))
        }
    postgres, mssql = produced["postgres"], produced["mssql"]
    assert list(postgres) == list(mssql) and len(postgres) >= len(_catalogue())
    declared = {config["entity"]: q.arrow_schema(_plan(config)) for config in _catalogue()}
    for name, left in postgres.items():
        right = mssql[name]
        entity = name.split("/")[2]
        assert left.schema_arrow.equals(right.schema_arrow, check_metadata=True), name
        assert left.schema_arrow.equals(declared[entity]), f"{name}: bronze names/types must be the catalogue's"
        key = lambda row: json.dumps(row, default=str, sort_keys=True)  # noqa: E731
        assert sorted(left.read().to_pylist(), key=key) == sorted(right.read().to_pylist(), key=key), name
    assert marks["postgres"] == marks["mssql"]


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(_PLATFORM_PREFIXES)}


def _agent(tmp_path: Path, *args: str, extra: str = "") -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "agent.toml"
    config.write_text(
        f'[agent]\ntenant_id = "{TENANT}"\nworkspace_id = "{WORKSPACE}"\nstate_dir = \'state\'\n{extra}', encoding="utf-8"
    )
    return subprocess.run(
        [sys.executable, str(AGENT), "--config", str(config), *args],
        env=_clean_env(), cwd=str(tmp_path), capture_output=True, text=True, check=False,
    )


def _secrets(fake: dict, dataset) -> list[str]:
    return [fake["password"], str(fake["host"]), fake["user"], *(c.schema for c in dataset.companies)]


def _expected_count(dataset, plan, alias: str, start: date, end: date) -> int:
    tables = dataset.tables[alias]
    rows = tables[plan.table]
    if not plan.date_field:
        return len(rows)
    if plan.parent:
        columns = b1.columns(plan.parent)
        dates = {r[columns.index(plan.parent_key)]: _as_date(r[columns.index(plan.date_field)]) for r in tables[plan.parent]}
        join = b1.columns(plan.table).index(plan.join_key)
        return sum(1 for r in rows if dates[r[join]] is not None and start <= dates[r[join]] < end)
    index = b1.columns(plan.table).index(plan.date_field)
    return sum(1 for r in rows if r[index] is not None and start <= _as_date(r[index]) < end)


def test_inventory_counts_equal_the_source_on_every_engine(b1_backend, dataset, tmp_path):
    proc = _agent(tmp_path, "inventory", "--json", "--months", "12")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    start = date.fromisoformat(report["window_start"])
    end = date.fromisoformat(report["window_end"])
    assert end == datetime.fromisoformat(report["source_clock"]).date() and report["failures"] == 0
    by_entity = {item["entity"]: item["companies"] for item in report["entities"]}
    for config in _catalogue():
        plan = _plan(config)
        for company in dataset.companies:
            expected = _expected_count(dataset, plan, company.alias, start, end)
            assert by_entity[plan.entity][company.alias] == {"rows": expected}, (plan.entity, company.alias)
    for secret in _secrets(b1_backend, dataset):
        assert secret not in proc.stdout and secret not in proc.stderr


def test_inventory_upload_writes_source_counts_on_every_engine(b1_backend, dataset, tmp_path):
    import pyarrow.parquet as pq

    from app.services import source_counts_mapping as mapping

    out = tmp_path / "out"
    entities = ("OINV", "INV1", "OITW", "OADM")
    args = [arg for entity in entities for arg in ("--entity", entity)]
    proc = _agent(tmp_path, "inventory", "--upload", "--output-dir", str(out), "--months", "3", *args)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    files = sorted(out.rglob("*.parquet"))
    assert [f.name for f in files] == ["SourceCounts.parquet"]
    layout = rf"raw/sap_b1/SourceCounts/tenant_id={TENANT}/workspace_id={WORKSPACE}/load_date=\d{{4}}-\d{{2}}-\d{{2}}/batch_id=[0-9a-f-]{{36}}/SourceCounts.parquet"
    assert re.fullmatch(layout, files[0].relative_to(out).as_posix())
    table = pq.ParquetFile(files[0]).read()
    assert table.schema.equals(mapping.arrow_schema(), check_metadata=True)
    rows = table.to_pylist()
    assert len(rows) == len(entities) * len(dataset.companies)
    counted_at = rows[0]["counted_at"]
    window_end = datetime.combine(counted_at.date(), datetime.min.time())
    start = mapping.count_window(counted_at, 3)[0]
    for row in rows:
        plan = _plan(_config(row["entity"]))
        assert row["error"] is None and row["counted_at"] == counted_at and row["window_end"] == window_end
        assert row["window_start"] == (datetime.combine(start, datetime.min.time()) if plan.date_field else None)
        assert row["source_rows"] == _expected_count(dataset, plan, row["_company"], start, window_end.date()), row
        assert row["_source_entity"] == "SourceCounts" and row["_load_type"] == "full" and row["_source_updated_at"] is None
    assert "SourceCounts: success" in proc.stdout


def test_test_connection_works_on_every_engine_without_leaking_secrets(b1_backend, dataset, tmp_path):
    from app.core.b1_source import B1Client

    proc = _agent(tmp_path, "test-connection", "--output-dir", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.startswith("dialect: "), "the test bed's user is literally 'postgres', so the name may be scrubbed"
    info = B1Client().test_connection()
    assert info["status"] == "ok" and info["dialect"] == b1_backend["dialect"]
    versions = {c.alias: dataset.tables[c.alias]["CINF"][0][0] for c in dataset.companies}
    assert {c["alias"]: c["b1_version"] for c in info["companies"]} == versions
    for alias, version in versions.items():
        assert f"company {alias}: reachable, Business One version {version}" in proc.stdout
    shown = repr({k: v for k, v in info.items() if k != "dialect"})
    for secret in _secrets(b1_backend, dataset):
        assert secret not in proc.stdout and secret not in shown


def test_errors_are_sanitized_on_every_engine(b1_backend, dataset, tmp_path, monkeypatch):
    from app.core.b1_source import B1Client, B1SourceError, CartridgeCircuitBreaker

    CartridgeCircuitBreaker.reset()
    client = B1Client()
    company = dataset.companies[0]
    missing_column = f'SELECT "Nope" FROM {client.config.strategy.table_ref(company.schema, "OINV")}'
    with pytest.raises(B1SourceError) as failed_query:
        client.fetch_all(missing_column)
    missing_object = f'SELECT 1 FROM {client.config.strategy.table_ref(company.schema, "NOPE")}'
    with pytest.raises(B1SourceError) as failed_object:
        client.fetch_all(missing_object)
    texts = [str(failed_query.value), str(failed_object.value)]
    assert CartridgeCircuitBreaker.snapshot()["failures"] == 0, "a SQL error is not a connectivity failure"

    monkeypatch.setenv("SAP_B1_PASSWORD", "Wrong-Password-4-Test!")
    denied = B1Client().test_connection()
    assert denied["status"] == "auth_error", denied
    texts.append(json.dumps({k: v for k, v in denied.items() if k != "dialect"}))
    monkeypatch.setenv("SAP_B1_PASSWORD", b1_backend["password"])

    monkeypatch.setenv("SAP_B1_PORT", "1")
    with pytest.raises(B1SourceError) as failed_connect:
        B1Client().fetch_all("SELECT 1")
    texts.append(str(failed_connect.value))
    CartridgeCircuitBreaker.reset()
    monkeypatch.setenv("SAP_B1_PORT", str(b1_backend["port"]))

    monkeypatch.setenv("SAP_B1_PASSWORD", "Wrong-Password-4-Test!")
    proc = _agent(tmp_path, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(tmp_path / "out"))
    assert proc.returncode == 1
    log = (tmp_path / "state" / "logs" / "agent.log").read_text(encoding="utf-8")
    texts.extend([proc.stdout, proc.stderr, log])
    for text in texts:
        for secret in [*_secrets(b1_backend, dataset), "Wrong-Password-4-Test!"]:
            assert secret not in text, (secret[:3], text[:300])


def test_date_helpers_agree_with_python_on_every_engine(b1_backend, dataset):
    from app.core.b1_source import B1Client

    client = B1Client()
    strategy = client.config.strategy
    reference = datetime(2026, 3, 15)
    with client.connection() as connection:
        today = connection.source_now().date()
        for company in dataset.companies:
            table = strategy.table_ref(company.schema, "OINV")
            column = 't."DocDate"'
            index = b1.columns("OINV").index("DocDate")
            dates = [_as_date(row[index]) for row in dataset.tables[company.alias]["OINV"]]
            _c, rows = connection.fetch_all(
                f"SELECT COUNT(*) FROM {table} t WHERE {strategy.days_between(column, '?')} BETWEEN 0 AND 29", [reference]
            )
            assert rows[0][0] == sum(1 for d in dates if 0 <= (reference.date() - d).days <= 29)
            _c, rows = connection.fetch_all(
                f"SELECT COUNT(*) FROM {table} t WHERE t.\"DocDate\" >= {strategy.add_days('?', -30)} AND t.\"DocDate\" < ?",
                [reference, reference],
            )
            assert rows[0][0] == sum(1 for d in dates if reference.date() - timedelta(days=30) <= d < reference.date())
            _c, rows = connection.fetch_all(f"SELECT COUNT(*) FROM {table} t WHERE {strategy.age_in_days(column)} >= 0")
            assert rows[0][0] == sum(1 for d in dates if d <= today)


def test_the_heartbeat_probe_pings_every_engine(b1_backend):
    from app.core.b1_source import CartridgeCircuitBreaker, probe_source, resolve_config

    CartridgeCircuitBreaker.reset()
    config = resolve_config()
    ok = probe_source(config)
    assert ok["ok"] is True and ok["error"] is None and ok["ms"] >= 0
    config.port = 1
    down = probe_source(config)
    assert down["ok"] is False and len(down["error"]) <= 200
    assert str(b1_backend["host"]) not in down["error"] and b1_backend["password"] not in down["error"]
    assert CartridgeCircuitBreaker.snapshot()["failures"] == 0, "the heartbeat probe never trips the breaker"


def test_every_company_is_read_from_its_own_schema_or_database(b1_backend, dataset, monkeypatch):
    by_company: dict[str, int] = defaultdict(int)
    recorder, es = _install(monkeypatch)
    es.run_entity(_config("OADM", mode="full"))
    for row in recorder.rows:
        by_company[row["_company"]] += 1
    assert dict(by_company) == {c.alias: len(dataset.tables[c.alias]["OADM"]) for c in dataset.companies}
    codes = {row["_company"]: row["Code"] for row in recorder.rows}
    assert codes == {c.alias: dataset.tables[c.alias]["OADM"][0][0] for c in dataset.companies}

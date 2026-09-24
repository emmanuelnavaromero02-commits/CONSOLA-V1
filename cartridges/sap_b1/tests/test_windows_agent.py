"""The Windows push agent writes exactly what the cartridge writes.

The agent in ``connect/windows-agent/agent.py`` runs the cartridge's own
reader (``b1_reader.read_entity``) with a SQLite state and a local spool
instead of the platform Postgres and the lakehouse. These tests run it
against the Business One-shaped Postgres fake with ``--output-dir`` and
check the files, the watermarks, the run log and the log file against the
cartridge's own rules; the subprocess runs get an environment stripped of
every platform variable, so they also prove the agent needs none of them.

Docker-backed tests skip without Docker (see ``fake_postgres``); the static
checks on the IAM policy and the install scripts always run.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

b1 = importlib.import_module("sap_b1_fake.schema")

CARTRIDGE_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = CARTRIDGE_ROOT / "connect" / "windows-agent"
AGENT = AGENT_DIR / "agent.py"
ENTITIES = CARTRIDGE_ROOT / "app" / "config" / "entities.yaml"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
SCOPE = f"tenant_id={TENANT}/workspace_id={WORKSPACE}/"
# Test-only upload credentials; the secret must never reach a log, a run row or the spool ledger.
TEST_ACCESS_KEY = "agent-test-access-key-id"
TEST_SECRET = "agent-test-secret-access-key-value"
# Nothing of the platform reaches the agent process.
_PLATFORM_PREFIXES = (
    "DATABASE_URL", "MINIO_", "LAKEHOUSE_", "FIELD_ENCRYPTION_KEY", "INTERNAL_API_KEY",
    "SECURITY_CONTEXT", "AWS_", "OMEGA_", "APP_ENV", "S3_BUCKET", "GCS_",
)
_STAMP = "%Y-%m-%dT%H:%M:%S"
SPOOL_NAME = re.compile(r"^[0-9a-f]{20}\.parquet$")


# ── helpers ────────────────────────────────────────────────────────────────


def _entity_config(entity: str) -> dict:
    with ENTITIES.open(encoding="utf-8") as handle:
        return next(dict(e) for e in yaml.safe_load(handle)["entities"] if e["entity"] == entity)


def _write_config(tmp_path: Path, extra: str = "", upload: bool = False) -> Path:
    """agent.toml with the scope and a relative state dir; ``extra`` lines
    belong to ``[agent]``; ``upload=True`` adds an ``[upload]`` section (the
    uploader itself is always a fake in these tests)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "agent.toml"
    text = (
        "[agent]\n"
        f'tenant_id = "{TENANT}"\n'
        f'workspace_id = "{WORKSPACE}"\n'
        "state_dir = 'state'\n"
        f"{extra}"
    )
    if upload:
        text += (
            "\n[upload]\n"
            'bucket = "agent-test-bucket"\n'
            'region = "us-east-1"\n'
            f'access_key_id = "{TEST_ACCESS_KEY}"\n'
            f'secret_access_key = "{TEST_SECRET}"\n'
        )
    config.write_text(text, encoding="utf-8")
    return config


def _clean_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_PLATFORM_PREFIXES)}
    env.update(overrides)
    assert "DATABASE_URL" not in env and "FIELD_ENCRYPTION_KEY" not in env
    return env


def _run(
    config: Path, *args: str, env: dict[str, str] | None = None, agent_path: Path = AGENT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(agent_path), "--config", str(config), *args],
        env=env or _clean_env(),
        cwd=str(config.parent),
        capture_output=True,
        text=True,
        check=False,
    )


def _spool_rows(config: Path, table: str = "spool") -> list[dict]:
    with _db(config) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _spool_files(config: Path) -> list[Path]:
    return sorted(p for p in (config.parent / "state" / "spool").glob("*") if p.is_file())


def _pg(dsn: str, sql: str, params=None, fetch: bool = False):
    """Edit or read the fake through the very connection facts it runs on."""
    import psycopg2

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _ps_array(name: str) -> list[str]:
    """The quoted items of ``$name = @( ... )`` in install.ps1, with forward slashes."""
    text = (AGENT_DIR / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"\${name}\s*=\s*@\((.*?)\)", text, flags=re.DOTALL)
    assert match, f"${name} not found in install.ps1"
    return [item.replace("\\", "/") for item in re.findall(r"'([^']+)'", match.group(1))]


class _FakeUploader:
    """Stands in for ``S3Uploader`` inside the loaded agent module: one
    ``upload`` call is one file after the real class's own retries, so
    ``error`` is what the spool sees (an UploadError or an UploadRejected)."""

    error: Exception | None = None
    calls: list[tuple[str, str]] = []

    def __init__(self, config, log, *, scope: str, client=None) -> None:
        self.config = config
        self.scope = scope

    def probe(self) -> None:
        type(self).calls.append(("probe", self.scope))

    def upload(self, path: Path, object_name: str) -> None:
        type(self).calls.append((Path(path).name, object_name))
        if type(self).error is not None:
            raise type(self).error


@pytest.fixture
def fake_uploader(agent, monkeypatch):
    _FakeUploader.error = None
    _FakeUploader.calls = []
    monkeypatch.setattr(agent, "S3Uploader", _FakeUploader)
    for name in ("OMEGA_AGENT_STATE_DIR", "OMEGA_S3_BUCKET", "OMEGA_TENANT_ID", "OMEGA_WORKSPACE_ID", "OMEGA_S3_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    return _FakeUploader


def _uploads(fake) -> list[tuple[str, str]]:
    return [call for call in fake.calls if call[0] != "probe"]


def _db(config: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(config.parent / "state" / "agent-state.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def _watermarks(config: Path) -> dict[str, str]:
    with _db(config) as conn:
        return {r["entity_name"]: r["last_watermark_value"] for r in conn.execute("SELECT * FROM entity_watermarks")}


def _runs(config: Path) -> list[dict]:
    with _db(config) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM extraction_runs ORDER BY started_at, rowid")]


def _log_text(config: Path) -> str:
    return (config.parent / "state" / "logs" / "agent.log").read_text(encoding="utf-8")


def _col(table: str, name: str) -> int:
    return b1.columns(table).index(name)


def _stamp(table: str, row: tuple) -> datetime:
    day = row[_col(table, "UpdateDate")]
    ts = row[_col(table, "UpdateTS")]
    return datetime(day.year, day.month, day.day, ts // 10000, ts // 100 % 100, ts % 100)


def _fmt(stamp: datetime) -> str:
    return stamp.strftime(_STAMP)


def _latest(dataset, table: str) -> dict[str, datetime]:
    return {c.alias: max(_stamp(table, row) for row in dataset.tables[c.alias][table]) for c in dataset.companies}


def _platform_watermarks(monkeypatch, entity_config: dict) -> dict[str, str]:
    """What ``extraction_service.run_entity`` records for the same read."""
    from app.services import extraction_service as es

    marks: dict[str, str] = {}
    monkeypatch.setattr(es, "create_run", lambda **kw: "run-platform")
    monkeypatch.setattr(es, "finish_run", lambda **kw: None)
    monkeypatch.setattr(es, "fail_run", lambda **kw: None)
    monkeypatch.setattr(es, "get_watermark", lambda key: marks.get(key))
    monkeypatch.setattr(
        es, "update_watermark", lambda **kw: marks.__setitem__(kw["entity_name"], kw["last_watermark_value"])
    )
    monkeypatch.setattr(es, "write_parquet_and_upload", lambda **kw: "s3://lakehouse/ignored")
    es.run_entity(entity_config)
    return marks


@pytest.fixture
def agent():
    """A fresh load of agent.py per test, after the conftest re-pinned ``app``,
    so patches on ``app.core.b1_source`` reach the code the agent runs."""
    name = "sap_b1_windows_agent_under_test"
    spec = importlib.util.spec_from_file_location(name, AGENT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


# ── files: schema and layout ───────────────────────────────────────────────


@pytest.mark.parametrize("entity", ["OINV", "INV1"])
def test_full_load_writes_the_cartridge_schema_and_layout(b1_env, dataset, tmp_path, entity):
    import pyarrow.parquet as pq

    from app.services import b1_queries as q

    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "extract", "--entity", entity, "--mode", "full", "--output-dir", str(out))
    assert proc.returncode == 0, proc.stderr

    runs = _runs(config)
    assert len(runs) == 1 and runs[0]["status"] == "success" and runs[0]["run_type"] == "full"
    run_id = runs[0]["run_id"]
    expected_total = sum(len(dataset.tables[c.alias][entity]) for c in dataset.companies)
    assert runs[0]["records_extracted"] == expected_total > 0

    files = sorted(out.rglob("*.parquet"))
    assert files and len(files) == runs[0]["batches"]
    layout = re.compile(
        rf"^raw/sap_b1/{entity}/tenant_id={TENANT}/workspace_id={WORKSPACE}/"
        rf"load_date=\d{{4}}-\d{{2}}-\d{{2}}/batch_id=(?P<batch>{re.escape(run_id)}(-b\d+)?)/{entity}\.parquet$"
    )
    declared = q.arrow_schema(q.plan_from_config(_entity_config(entity)))
    batch_ids: list[str] = []
    rows: list[dict] = []
    for path in files:
        match = layout.match(path.relative_to(out).as_posix())
        assert match, path
        batch_ids.append(match.group("batch"))
        table = pq.read_table(path)
        assert table.schema.equals(declared, check_metadata=True), path
        for row in table.to_pylist():
            assert row["_run_id"] == match.group("batch")
            rows.append(row)
    assert batch_ids == [run_id, *(f"{run_id}-b{n}" for n in range(1, len(files)))]

    assert len(rows) == expected_total
    by_company = defaultdict(int)
    for row in rows:
        by_company[row["_company"]] += 1
        assert row["_source_entity"] == entity and row["_load_type"] == "full"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row["_extracted_at"])
        assert row["_source_updated_at"]
    assert dict(by_company) == {c.alias: len(dataset.tables[c.alias][entity]) for c in dataset.companies}

    if entity == "OINV":
        # The watermark column is on the row: the platform stores str(driver value).
        assert all(row["_watermark_value"] == str(row["UpdateDate"]) for row in rows)
        assert all(row["_source_updated_at"] == _fmt(datetime.combine(row["UpdateDate"].date(), datetime.min.time())
                                                     + timedelta(seconds=row["UpdateTS"] // 10000 * 3600
                                                                 + row["UpdateTS"] // 100 % 100 * 60
                                                                 + row["UpdateTS"] % 100)) for row in rows)
    else:
        # Lines carry no UpdateDate of their own and inherit the header stamp.
        assert all(row["_watermark_value"] is None for row in rows)
        stamps = {
            (c.alias, row[_col("OINV", "DocEntry")]): _fmt(_stamp("OINV", row))
            for c in dataset.companies
            for row in dataset.tables[c.alias]["OINV"]
        }
        assert all(row["_source_updated_at"] == stamps[(row["_company"], row["DocEntry"])] for row in rows)

    log = _log_text(config)
    assert b1_env["password"] not in log and b1_env["host"] not in log
    assert all(c.schema not in log for c in dataset.companies)
    assert f"run {run_id}" in log


def test_agent_files_match_the_cartridge_writer_byte_for_byte_in_schema(b1_env, dataset, tmp_path, monkeypatch):
    """Same records through the agent and through ``parquet_service``: the
    schema (with metadata), the writer signature and the row-group layout
    are identical. Only the run id and the extraction instant differ."""
    import shutil

    import pyarrow.parquet as pq

    from app.core.b1_source import B1Client
    from app.services import b1_queries as q
    from app.services import parquet_service

    config = _write_config(tmp_path)
    out = tmp_path / "out"
    company = dataset.companies[0]
    monkeypatch.setenv("SAP_B1_COMPANIES", f"{company.alias}={company.schema}")
    proc = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(out))
    assert proc.returncode == 0, proc.stderr
    agent_files = sorted(out.rglob("*.parquet"))
    assert len(agent_files) == 1
    agent_file = pq.ParquetFile(agent_files[0])

    plan = q.plan_from_config(_entity_config("OINV"))
    columns, rows = B1Client().fetch_all(*q.select_sql(plan, company.schema, mode="full"))
    records = q.rows_to_records(plan, company.alias, columns, rows)
    written: list[Path] = []
    monkeypatch.setattr(
        parquet_service,
        "upload_file_to_minio",
        lambda *, local_path, object_name: written.append(Path(shutil.copy(local_path, tmp_path / "platform.parquet"))),
    )
    parquet_service.write_parquet_and_upload(
        entity="OINV", rows=records, run_id=_runs(config)[0]["run_id"], load_type="full",
        watermark_field=plan.watermark_field, expected_columns=list(plan.output_columns), arrow_schema=q.arrow_schema(plan),
    )
    platform_file = pq.ParquetFile(written[0])

    assert agent_file.schema_arrow.equals(platform_file.schema_arrow, check_metadata=True)
    assert agent_file.metadata.created_by == platform_file.metadata.created_by
    assert agent_file.metadata.num_rows == platform_file.metadata.num_rows == len(records)
    assert agent_file.metadata.num_row_groups == platform_file.metadata.num_row_groups
    agent_rows = agent_file.read().to_pylist()
    platform_rows = platform_file.read().to_pylist()
    volatile = {"_extracted_at"}
    assert [{k: v for k, v in r.items() if k not in volatile} for r in agent_rows] == [
        {k: v for k, v in r.items() if k not in volatile} for r in platform_rows
    ]


# ── watermarks ─────────────────────────────────────────────────────────────


def test_a_second_incremental_run_writes_nothing(b1_env, dataset, tmp_path, agent):
    config = _write_config(tmp_path)
    first = tmp_path / "first"
    proc = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(first))
    assert proc.returncode == 0, proc.stderr
    marks = _watermarks(config)
    assert set(marks) == {f"OINV@{c.alias}" for c in dataset.companies}

    # Nothing changed since the last cycle: move every company past its
    # newest stamp (plus the 5 minute back-off), as time would.
    state = agent.AgentState(config.parent / "state" / "agent-state.sqlite")
    seeded = {}
    for alias, latest in _latest(dataset, "OINV").items():
        seeded[f"OINV@{alias}"] = _fmt(latest + timedelta(minutes=6))
        state.update_watermark(f"OINV@{alias}", "UpdateDate", seeded[f"OINV@{alias}"], "seed")
    state.close()
    assert _watermarks(config) == seeded

    second = tmp_path / "second"
    proc = _run(config, "extract", "--entity", "OINV", "--mode", "incremental", "--output-dir", str(second))
    assert proc.returncode == 0, proc.stderr
    assert list(second.rglob("*.parquet")) == []
    runs = _runs(config)
    assert len(runs) == 2
    assert runs[1]["status"] == "success" and runs[1]["run_type"] == "incremental"
    assert runs[1]["records_extracted"] == 0 and runs[1]["batches"] == 0 and not runs[1]["error_message"]
    assert _watermarks(config) == seeded


def test_the_stored_watermark_is_what_the_cartridge_would_store(b1_env, dataset, tmp_path, monkeypatch, agent):
    """Max stamp seen, capped at the source clock read when the run started:
    the agent's SQLite row equals ``extraction_service``'s recorded value
    under the same clock, on both sides of the cap."""
    from app.core import b1_source

    latest = _latest(dataset, "OINV")
    config = _write_config(tmp_path)

    # The clock lies before the newest stamps: every company is capped.
    frozen = min(latest.values()) - timedelta(days=30)
    monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: frozen)
    code = agent.main(["--config", str(config), "--quiet", "extract", "--entity", "OINV", "--mode", "full",
                       "--output-dir", str(tmp_path / "capped")])
    assert code == 0
    capped = _watermarks(config)
    assert capped == _platform_watermarks(monkeypatch, {**_entity_config("OINV"), "mode": "full"})
    assert capped == {f"OINV@{alias}": _fmt(frozen) for alias in latest}
    assert _runs(config)[-1]["source_clock"] == _fmt(frozen)

    # The clock lies after every stamp: the exact maximum per company.
    monkeypatch.setattr(b1_source.Connection, "source_now", lambda self: datetime(2099, 1, 1))
    code = agent.main(["--config", str(config), "--quiet", "extract", "--entity", "OINV", "--mode", "full",
                       "--output-dir", str(tmp_path / "exact")])
    assert code == 0
    exact = _watermarks(config)
    assert exact == _platform_watermarks(monkeypatch, {**_entity_config("OINV"), "mode": "full"})
    assert exact == {f"OINV@{alias}": _fmt(stamp) for alias, stamp in latest.items()}

    # And a line table records its header's stamp, like the cartridge.
    code = agent.main(["--config", str(config), "--quiet", "extract", "--entity", "INV1", "--mode", "full",
                       "--output-dir", str(tmp_path / "lines")])
    assert code == 0
    assert {k: v for k, v in _watermarks(config).items() if k.startswith("INV1@")} == {
        f"INV1@{alias}": _fmt(stamp) for alias, stamp in latest.items()
    }


# ── intercompany mapping ───────────────────────────────────────────────────


def _mapping(dataset) -> str:
    """The test bed's own intercompany codes, as the customer would configure them."""
    generator = importlib.import_module("sap_b1_fake.generator")
    aliases = {c.alias for c in dataset.companies}
    entries = [f"mx_mfg:{code}={alias}" for alias, code in sorted(generator.INTERCOMPANY_CUSTOMER.items()) if alias in aliases]
    entries += [f"{alias}:{generator.INTERCOMPANY_SUPPLIER}=mx_mfg" for alias in sorted(generator.INTERCOMPANY_CUSTOMER) if alias in aliases]
    return ",".join(entries)


def test_extract_all_writes_the_intercompany_mapping_last_like_the_cartridge(b1_env, dataset, tmp_path):
    import pyarrow.parquet as pq

    from app.services import intercompany_mapping as icm

    mapping = _mapping(dataset)
    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "extract-all", "--entity", "CINF", "--mode", "full", "--output-dir", str(out),
                env=_clean_env(SAP_B1_INTERCOMPANY=mapping))
    assert proc.returncode == 0, proc.stderr

    runs = _runs(config)
    assert [r["entity_name"] for r in runs] == ["CINF", icm.ENTITY], "the mapping lands after the tables"
    ic_run = runs[-1]
    assert ic_run["status"] == "success" and ic_run["run_type"] == "full" and ic_run["batches"] == 1
    expected = icm.partner_records(icm.parse_intercompany(mapping))
    assert ic_run["records_extracted"] == len(expected) > 0

    files = sorted(out.rglob("IntercompanyPartners.parquet"))
    assert len(files) == 1
    layout = re.compile(
        rf"^raw/sap_b1/{icm.ENTITY}/tenant_id={TENANT}/workspace_id={WORKSPACE}/"
        rf"load_date=\d{{4}}-\d{{2}}-\d{{2}}/batch_id={re.escape(ic_run['run_id'])}/{icm.ENTITY}\.parquet$"
    )
    assert layout.match(files[0].relative_to(out).as_posix()), files[0]
    table = pq.read_table(files[0])
    # The very schema the cartridge hands its writer (refresh_intercompany_partners).
    assert table.schema.equals(icm.arrow_schema(), check_metadata=True)
    rows = table.to_pylist()
    assert [(r["_company"], r["CardCode"], r["CounterpartyCompany"], r["MappingSource"]) for r in rows] == [
        (r["_company"], r["CardCode"], r["CounterpartyCompany"], r["MappingSource"]) for r in expected
    ]
    assert all(r["_source_entity"] == icm.ENTITY and r["_load_type"] == "full" for r in rows)
    assert all(r["_run_id"] == ic_run["run_id"] and r["_watermark_value"] is None and r["_source_updated_at"] is None for r in rows)
    assert "IntercompanyPartners@" not in " ".join(_watermarks(config)), "a snapshot records no watermark"

    # Skipped on request, and written alone on request: the operator can
    # correct the mapping without re-reading a single table.
    proc = _run(config, "extract-all", "--entity", "CINF", "--mode", "full", "--skip-intercompany",
                "--output-dir", str(tmp_path / "skip"), env=_clean_env(SAP_B1_INTERCOMPANY=mapping))
    assert proc.returncode == 0, proc.stderr
    assert list((tmp_path / "skip").rglob("IntercompanyPartners.parquet")) == []
    proc = _run(config, "refresh-intercompany", "--output-dir", str(tmp_path / "alone"),
                env=_clean_env(SAP_B1_INTERCOMPANY=mapping))
    assert proc.returncode == 0, proc.stderr
    alone = list((tmp_path / "alone").rglob("*.parquet"))
    assert len(alone) == 1 and alone[0].name == "IntercompanyPartners.parquet"
    assert _runs(config)[-1]["entity_name"] == icm.ENTITY


def test_an_empty_mapping_is_a_zero_row_typed_file(b1_env, dataset, tmp_path):
    """"No group partners" is an answer silver can join, not a missing file."""
    import pyarrow.parquet as pq

    from app.services import intercompany_mapping as icm

    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "refresh-intercompany", "--output-dir", str(out))
    assert proc.returncode == 0, proc.stderr
    files = list(out.rglob("IntercompanyPartners.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])
    assert table.num_rows == 0 and table.schema.equals(icm.arrow_schema(), check_metadata=True)
    assert _runs(config)[-1]["records_extracted"] == 0 and _runs(config)[-1]["status"] == "success"


def test_a_mapping_naming_an_unknown_company_is_a_configuration_error(b1_env, dataset, tmp_path):
    config = _write_config(tmp_path)
    proc = _run(config, "refresh-intercompany", "--output-dir", str(tmp_path / "out"),
                env=_clean_env(SAP_B1_INTERCOMPANY="mx_mfg:C-IC-X=mx_nowhere"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "intercompany" in proc.stderr and "mx_nowhere" in proc.stderr
    assert list((tmp_path / "out").rglob("*.parquet")) == []
    proc = _run(config, "refresh-intercompany", "--output-dir", str(tmp_path / "out"),
                env=_clean_env(SAP_B1_INTERCOMPANY="not a mapping"))
    assert proc.returncode == 2
    assert "company:CARDCODE=counterparty" in proc.stderr


# ── failures and logs ──────────────────────────────────────────────────────


def test_a_failing_connection_is_a_failed_run_with_a_non_zero_exit(b1_env, dataset, tmp_path):
    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(out),
                env=_clean_env(SAP_B1_PORT="1"))
    assert proc.returncode == 1, proc.stderr
    assert list(out.rglob("*.parquet")) == []
    runs = _runs(config)
    assert len(runs) == 1 and runs[0]["status"] == "failed" and runs[0]["finished_at"]
    assert runs[0]["error_message"] and "connection failed" in runs[0]["error_message"]
    assert _watermarks(config) == {}

    wrong = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(out),
                 env=_clean_env(SAP_B1_PASSWORD="not-the-password-either"))
    assert wrong.returncode == 1
    assert [r["status"] for r in _runs(config)] == ["failed", "failed"]

    log = _log_text(config)
    for text in (log, proc.stderr, proc.stdout, wrong.stderr, wrong.stdout, json.dumps(_runs(config)),
                 json.dumps(_spool_rows(config)), json.dumps(_spool_rows(config, "spool_lost"))):
        assert b1_env["password"] not in text
        assert "not-the-password-either" not in text
        assert b1_env["host"] not in text
        assert b1_env["user"] not in text
        assert all(c.schema not in text for c in dataset.companies)
    assert "failed" in log
    assert _spool_rows(config) == [], "a local delivery never touches the spool ledger"


def test_test_connection_reports_every_company_without_secrets(b1_env, dataset, tmp_path):
    config = _write_config(tmp_path)
    proc = _run(config, "test-connection", "--output-dir", str(tmp_path / "out"))
    assert proc.returncode == 0, proc.stderr
    for company in dataset.companies:
        assert f"company {company.alias}: reachable, Business One version" in proc.stdout
        assert company.schema not in proc.stdout
    assert b1_env["password"] not in proc.stdout and b1_env["host"] not in proc.stdout
    assert "upload: check skipped" in proc.stdout

    down = _run(config, "test-connection", "--output-dir", str(tmp_path / "out"), env=_clean_env(SAP_B1_PORT="1"))
    assert down.returncode == 1 and "unreachable" in down.stdout
    assert b1_env["host"] not in down.stdout and b1_env["password"] not in down.stdout


def test_status_lists_watermarks_and_runs(b1_env, dataset, tmp_path):
    config = _write_config(tmp_path)
    assert _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(tmp_path / "out")).returncode == 0
    proc = _run(config, "status", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert {row["entity_name"] for row in report["watermarks"]} == {f"OINV@{c.alias}" for c in dataset.companies}
    assert report["runs"][0]["status"] == "success" and report["spool_pending"] == 0
    assert report["spool_lost"] == 0 and report["pending_files"] == [] and report["lost_files"] == []
    text = _run(config, "status").stdout
    assert "OINV" in text and b1_env["host"] not in text and b1_env["password"] not in text
    assert "spool pending: 0; lost: 0" in text


def test_placeholders_and_missing_configuration_are_configuration_errors(tmp_path):
    config = tmp_path / "agent.toml"
    config.write_text(
        '[agent]\ntenant_id = "<TENANT_ID>"\nworkspace_id = "w"\nstate_dir = \'state\'\n'
        '[source]\nhost = "<HANA_HOST>"\n',
        encoding="utf-8",
    )
    env = {k: v for k, v in _clean_env().items() if not k.startswith("SAP_B1_")}
    proc = _run(config, "status", env=env)
    assert proc.returncode == 2 and "placeholder" in proc.stderr
    # An error in agent.toml itself happens before the real logging exists;
    # it still lands in agent.log of the state dir the file names (the
    # scheduled task shows nothing but the exit code).
    bootstrap_log = tmp_path / "state" / "logs" / "agent.log"
    assert bootstrap_log.is_file()
    logged = bootstrap_log.read_text("utf-8")
    assert re.search(r"ERROR omega-sap-b1-agent: configuration: \w+: replace the template placeholder <[A-Z_]+>", logged), logged

    config.write_text('[agent]\ntenant_id = "t"\nworkspace_id = "w"\nstate_dir = \'state\'\n', encoding="utf-8")
    proc = _run(config, "extract", "--entity", "OINV", "--output-dir", str(tmp_path / "out"), env=env)
    assert proc.returncode == 2 and "SAP_B1_HOST" in proc.stderr and "SAP_B1_PASSWORD" in proc.stderr

    proc = _run(config, "extract", "--entity", "NOPE", "--output-dir", str(tmp_path / "out"), env=env)
    assert proc.returncode == 2 and "NOPE" in proc.stderr

    # Unparseable TOML: the state dir cannot be read from it, so the line
    # goes to logs/ next to the file (the installed layout keeps agent.toml
    # in the state dir, so that is the same agent.log).
    config.write_text('[agent\ntenant_id = "t"\n', encoding="utf-8")
    proc = _run(config, "status", env=env)
    assert proc.returncode == 2 and "not valid TOML" in proc.stderr
    assert "configuration: configuration file is not valid TOML" in (tmp_path / "logs" / "agent.log").read_text("utf-8")


def test_the_agent_needs_nothing_from_the_platform():
    """agent.py imports only the pure cartridge modules: no platform
    settings, no Postgres, no MinIO, no pandas."""
    probe = (
        "import sys, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('probe_agent', {str(AGENT)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['probe_agent'] = m; spec.loader.exec_module(m)\n"
        "loaded = sorted(n for n in sys.modules if n.split('.')[0] in "
        "{'pandas', 'minio', 'sqlalchemy', 'pydantic', 'pydantic_settings', 'requests', 'cryptography', 'psycopg2', 'boto3'})\n"
        "print('LOADED=' + ','.join(loaded))\n"
        "print('ENTITIES=%d' % len(m.load_catalogue()))\n"
    )
    env = {k: v for k, v in _clean_env().items() if not k.startswith("SAP_B1_")}
    proc = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LOADED=\n" in proc.stdout and "ENTITIES=45" in proc.stdout


# ── static: policy and scripts ─────────────────────────────────────────────


def test_iam_policy_allows_only_the_bronze_prefix():
    """One key per customer: it writes and lists nothing but its own
    tenant/workspace scope under every entity of ``raw/sap_b1/``."""
    scoped = "raw/sap_b1/*/tenant_id=<TENANT_ID>/workspace_id=<WORKSPACE_ID>/*"
    policy = json.loads((AGENT_DIR / "iam-policy.template.json").read_text(encoding="utf-8"))
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    assert statements and all(s["Effect"] == "Allow" for s in statements)
    actions = {a for s in statements for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
    assert actions == {"s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket"}
    for statement in statements:
        acts = [statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"]
        resources = [statement["Resource"]] if isinstance(statement["Resource"], str) else statement["Resource"]
        if "s3:ListBucket" in acts:
            assert acts == ["s3:ListBucket"]
            assert resources == ["arn:aws:s3:::<BUCKET>"]
            assert statement["Condition"] == {"StringLike": {"s3:prefix": [scoped]}}
        else:
            assert resources == [f"arn:aws:s3:::<BUCKET>/{scoped}"]
            assert "Condition" not in statement or statement["Condition"] == {}
    text = json.dumps(policy)
    assert "GetObject" not in text and "DeleteObject" not in text and '"*"' not in text and "s3:*" not in text
    assert "raw/sap_b1/*\"" not in text, "an unscoped raw/sap_b1/* would let one customer's key write another's data"
    for token in re.findall(r"<[^<>\n]+>", text):
        assert re.fullmatch(r"<[A-Z][A-Z0-9_]*>", token), token


def test_install_scripts_and_templates_embed_no_credentials():
    scripts = {name: (AGENT_DIR / name).read_text(encoding="utf-8") for name in ("install.ps1", "run.ps1", "uninstall.ps1")}
    template = (AGENT_DIR / "agent.toml.template").read_text(encoding="utf-8")
    readme = (AGENT_DIR / "README.md").read_text(encoding="utf-8")
    everything = "\n".join([*scripts.values(), template, readme])

    assert not re.search(r"\bAKIA[0-9A-Z]{16}\b", everything)
    assert not re.search(r"(?i)secret[_ ]?access[_ ]?key\s*[=:]\s*['\"][A-Za-z0-9/+=]{20,}", everything)
    assert not re.search(r"(?i)password\s*[=:]\s*['\"](?!<)[^'\"\s]+['\"]", everything)

    for key in ("password", "access_key_id", "secret_access_key"):
        value = re.search(rf"^{key}\s*=\s*\"([^\"]*)\"", template, re.MULTILINE).group(1)
        assert re.fullmatch(r"<[A-Z_]+>", value), f"{key} in the template must stay a placeholder"

    install = scripts["install.ps1"]
    # The service account password only ever comes from an interactive prompt.
    assert "Get-Credential" in install and "Read-Host" not in install.replace("Read-Host -AsSecureString", "")
    assert not re.search(r"-Password\s+['\"]", install)
    assert not re.search(r"ConvertTo-SecureString\s+['\"]", install)
    # Python only from python.org, over TLS, and the config file is never overwritten.
    assert re.findall(r"https?://[^\s'\"]+", install) and all(
        url.startswith("https://www.python.org/") for url in re.findall(r"https?://[^\s'\"]+", install)
    )
    assert "icacls" in install and "Register-ScheduledTask" in install
    for script in scripts.values():
        assert "Set-ExecutionPolicy" not in script
    assert "Unregister-ScheduledTask" in scripts["uninstall.ps1"]
    assert "agent.py" in scripts["run.ps1"] and "extract-all" in scripts["run.ps1"]


# ── uploader: error classification and scope (no S3, a fake client) ───────


class _FakeS3Client:
    """The boto3 client surface the uploader touches: each ``upload_file``
    raises the next scripted exception, then succeeds."""

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.uploads: list[tuple[str, str, str, dict | None]] = []
        self.listed: list[dict] = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):  # noqa: N803 - boto3's spelling
        self.uploads.append((filename, bucket, key, ExtraArgs))
        if self.failures:
            raise self.failures.pop(0)

    def list_objects_v2(self, **kwargs):
        self.listed.append(kwargs)
        return {}


def _uploader(agent, client, attempts: int = 5):
    config = agent.UploadConfig(bucket="agent-test-bucket", max_attempts=attempts)
    return agent.S3Uploader(config, logging.getLogger("uploader-under-test"), scope=SCOPE, client=client)


def _object(entity: str = "OINV", run_id: str = "run-1") -> str:
    return f"raw/sap_b1/{entity}/{SCOPE}load_date=2026-01-01/batch_id={run_id}/{entity}.parquet"


@pytest.fixture
def sleeps(agent, monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


def test_a_rejected_key_is_reported_after_one_attempt_even_when_boto3_hides_the_code(agent, sleeps, tmp_path):
    """boto3 wraps the ClientError of a managed upload into
    S3UploadFailedError (no .response, no cause): the code must be read
    from the message, or a revoked key would be retried five times with
    sleeps on every file and the documented message would never appear."""
    from boto3.exceptions import S3UploadFailedError
    from botocore.exceptions import ClientError

    path = tmp_path / "x.parquet"
    path.write_bytes(b"not read")
    hidden = S3UploadFailedError(
        "Failed to upload x.parquet to agent-test-bucket/raw/sap_b1/x: An error occurred (AccessDenied)"
        " when calling the PutObject operation: Access Denied"
    )
    client = _FakeS3Client([hidden])
    with pytest.raises(agent.UploadRejected, match=r"upload rejected \(AccessDenied\)") as info:
        _uploader(agent, client).upload(path, _object())
    assert info.value.code == "AccessDenied"
    assert len(client.uploads) == 1 and sleeps == []

    # The code also travels on the response of a bare ClientError and on
    # the cause of a wrapping exception.
    for exc in (
        ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "PutObject"),
        _wrapped(ClientError({"Error": {"Code": "InvalidAccessKeyId", "Message": "bad"}}, "PutObject")),
    ):
        client = _FakeS3Client([exc])
        with pytest.raises(agent.UploadRejected):
            _uploader(agent, client).upload(path, _object())
        assert len(client.uploads) == 1
    assert sleeps == []


def _wrapped(cause: Exception) -> Exception:
    try:
        raise RuntimeError("transfer failed") from cause
    except RuntimeError as exc:
        return exc


def test_a_transient_failure_is_retried_with_backoff_then_succeeds(agent, sleeps, tmp_path):
    from boto3.exceptions import S3UploadFailedError
    from botocore.exceptions import ClientError

    path = tmp_path / "x.parquet"
    path.write_bytes(b"not read")
    client = _FakeS3Client([
        ClientError({"Error": {"Code": "SlowDown", "Message": "slow"}}, "PutObject"),
        S3UploadFailedError("Failed to upload: An error occurred (RequestTimeout) when calling the PutObject operation"),
        ConnectionError("reset"),
    ])
    _uploader(agent, client).upload(path, _object())
    assert len(client.uploads) == 4 and sleeps == [2.0, 4.0, 8.0]
    assert client.uploads[0][1:3] == ("agent-test-bucket", _object())

    client = _FakeS3Client([ConnectionError("down"), ConnectionError("down")])
    with pytest.raises(agent.UploadError, match="upload failed after 2 attempts: ConnectionError: down"):
        _uploader(agent, client, attempts=2).upload(path, _object())
    assert len(client.uploads) == 2 and sleeps == [2.0, 4.0, 8.0, 2.0]


def test_the_uploader_refuses_keys_outside_its_own_scope_and_probes_only_it(agent, tmp_path):
    path = tmp_path / "x.parquet"
    path.write_bytes(b"not read")
    client = _FakeS3Client()
    uploader = _uploader(agent, client)
    other_tenant = f"raw/sap_b1/OINV/tenant_id=99999999-9999-4999-8999-999999999999/workspace_id={WORKSPACE}/x.parquet"
    for name in (
        other_tenant,
        f"raw/sap_b1/OINV/tenant_id={TENANT}/workspace_id=other/x.parquet",
        f"raw/other/OINV/{SCOPE}x.parquet",
        f"raw/sap_b1/{SCOPE}x.parquet",
        f"raw/sap_b1/../OINV/{SCOPE}x.parquet",
        f"raw/sap_b1/OINV/{SCOPE}../../x.parquet",
        f"OINV/{SCOPE}x.parquet",
    ):
        with pytest.raises(agent.UploadError, match="refusing to upload outside"):
            uploader.upload(path, name)
    assert client.uploads == [], "a refused key never reaches the client"
    uploader.upload(path, _object("IntercompanyPartners"))
    assert len(client.uploads) == 1

    uploader.probe()
    assert client.listed == [{"Bucket": "agent-test-bucket", "Prefix": f"raw/sap_b1/CINF/{SCOPE}", "MaxKeys": 1}]
    # What probe lists is inside what the policy template allows.
    policy_prefix = "raw/sap_b1/*/tenant_id=<TENANT_ID>/workspace_id=<WORKSPACE_ID>/*"
    pattern = re.escape(policy_prefix.replace("<TENANT_ID>", TENANT).replace("<WORKSPACE_ID>", WORKSPACE)).replace(r"\*", ".*")
    assert re.fullmatch(pattern, client.listed[0]["Prefix"])
    assert re.fullmatch(pattern, _object("IntercompanyPartners"))


# ── spool: file names, durability, leftovers ───────────────────────────────


def test_spool_file_names_stay_short_and_local_delivery_keeps_the_bucket_layout(agent, tmp_path):
    """The Bronze key of an IntercompanyPartners batch is ~170 characters;
    under the template's default state dir the spool must still fit in
    Windows' 259-character MAX_PATH with room to spare, so spooled files
    get a short opaque name and only ``--output-dir`` lays out the bucket."""
    template = (AGENT_DIR / "agent.toml.template").read_text(encoding="utf-8")
    default_state_dir = re.search(r"^state_dir\s*=\s*'([^']+)'", template, flags=re.MULTILINE).group(1)
    longest_entity = max((e["entity"] for e in yaml.safe_load(ENTITIES.read_text("utf-8"))["entities"]), key=len)
    longest_entity = max((longest_entity, "IntercompanyPartners"), key=len)
    batch = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa-b9999"
    object_name = agent.bronze_parquet.bronze_object_name(longest_entity, SCOPE, "2026-12-31", batch)
    assert len(object_name) > 160

    log = logging.getLogger("spool-under-test")
    spooled = agent.Spool(tmp_path / "spool", state=None, uploader=object(), log=log)  # any uploader: not local
    target = spooled._target(object_name)
    assert target.parent == tmp_path / "spool" and SPOOL_NAME.match(target.name), target
    windows_path = f"{default_state_dir}\\spool\\{target.name}.part"
    assert len(windows_path) <= 80, windows_path
    assert len(f"{default_state_dir}\\spool\\{target.name}") <= 80

    local = agent.Spool(tmp_path / "out", state=None, uploader=None, log=log)
    assert local._target(object_name) == (tmp_path / "out").joinpath(*object_name.split("/"))


def test_batch_files_are_forced_to_disk_before_the_watermark_moves(b1_env, dataset, tmp_path, agent, monkeypatch):
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(agent.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    config = _write_config(tmp_path)
    code = agent.main(["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full",
                       "--output-dir", str(tmp_path / "out")])
    assert code == 0
    files = list((tmp_path / "out").rglob("*.parquet"))
    assert files and len(synced) >= len(files)
    assert not list((tmp_path / "out").rglob("*.part"))


# ── spool and uploads through the agent (a fake uploader) ──────────────────


def test_a_failed_upload_stays_in_the_spool_and_the_next_run_drains_it(b1_env, dataset, tmp_path, agent, fake_uploader):
    config = _write_config(tmp_path, upload=True)
    argv = ["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full"]
    # The error quotes both secrets the way a driver or SDK message might.
    fake_uploader.error = agent.UploadError(
        f"upload failed after 5 attempts: EndpointConnectionError: key {TEST_SECRET} password {b1_env['password']}"
    )
    assert agent.main(argv) == 1
    runs = _runs(config)
    assert len(runs) == 1 and runs[0]["status"] == "failed" and runs[0]["records_extracted"] == len(dataset.companies)
    assert "could not be uploaded and stay in the spool" in runs[0]["error_message"]
    batches = runs[0]["batches"]
    assert batches >= 1

    files = _spool_files(config)
    assert len(files) == batches and all(SPOOL_NAME.match(f.name) for f in files), [f.name for f in files]
    rows = _spool_rows(config)
    assert len(rows) == batches
    layout = re.compile(rf"^raw/sap_b1/CINF/{re.escape(SCOPE)}load_date=\d{{4}}-\d{{2}}-\d{{2}}/batch_id={re.escape(runs[0]['run_id'])}(-b\d+)?/CINF\.parquet$")
    for row in rows:
        assert layout.match(row["object_name"]), row["object_name"]
        assert Path(row["path"]) in files
        assert row["attempts"] == 1 and row["entity_name"] == "CINF" and row["run_id"] == runs[0]["run_id"]
        assert "EndpointConnectionError" in row["last_error"]
        assert TEST_SECRET not in row["last_error"] and b1_env["password"] not in row["last_error"]
        assert "***" in row["last_error"]
    assert len(_uploads(fake_uploader)) == batches
    log = _log_text(config)
    assert "kept in spool, will retry on the next run" in log
    assert TEST_SECRET not in log and b1_env["password"] not in log

    # status shows the queue with the bucket path, not the opaque file name.
    assert agent.main(["--config", str(config), "--quiet", "status"]) == 0
    proc = _run(config, "status", "--json")
    report = json.loads(proc.stdout)
    assert report["spool_pending"] == batches and report["spool_lost"] == 0
    assert [r["object_name"] for r in report["pending_files"]] == [r["object_name"] for r in rows]
    assert TEST_SECRET not in proc.stdout
    text = _run(config, "status").stdout
    assert f"spool pending: {batches}; lost: 0" in text and f"pending {rows[0]['object_name']} attempts=1" in text

    # The next run uploads the backlog first, then its own files; nothing is
    # re-read from the source because of the earlier failure.
    fake_uploader.error = None
    fake_uploader.calls = []
    assert agent.main(argv) == 0
    uploads = _uploads(fake_uploader)
    assert [name for _, name in uploads[:batches]] == [r["object_name"] for r in rows]
    assert len(uploads) == 2 * batches
    assert _spool_rows(config) == [] and _spool_files(config) == []
    assert [r["status"] for r in _runs(config)] == ["failed", "success"]
    assert f"spool: {batches} file(s) uploaded from earlier runs" in _log_text(config)


def test_the_pending_limit_stops_extraction_with_a_failed_exit(b1_env, dataset, tmp_path, agent, fake_uploader):
    config = _write_config(tmp_path, extra="max_pending_files = 2\n", upload=True)
    argv = ["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full"]
    fake_uploader.error = agent.UploadError("upload failed after 5 attempts: ConnectionError: down")
    assert agent.main(argv) == 1
    pending = len(_spool_rows(config))
    assert pending >= 2
    assert agent.main(argv) == 1
    assert len(_runs(config)) == 1, "no extraction while the queue is at the limit"
    assert [r["attempts"] for r in _spool_rows(config)] == [2] * pending
    assert f"spool holds {pending} pending files (limit 2); not extracting more until uploads work again" in _log_text(config)


def test_a_rejected_key_stops_every_upload_of_the_cycle(b1_env, dataset, tmp_path, agent, fake_uploader):
    """One attempt tells that the key is revoked; trying the next file, or
    sleeping between attempts, cannot change the answer within the cycle."""
    config = _write_config(tmp_path, upload=True)
    argv = ["--config", str(config), "--quiet", "extract-all", "--entity", "CINF", "--mode", "full"]
    fake_uploader.error = agent.UploadRejected(
        "upload rejected (AccessDenied); check the access key and the IAM policy for the bucket", "AccessDenied"
    )
    assert agent.main(argv) == 1
    rows = _spool_rows(config)
    assert len(rows) >= 2 and {r["entity_name"] for r in rows} == {"CINF", "IntercompanyPartners"}
    assert len(_uploads(fake_uploader)) == 1, "the first rejection suspends uploads for the cycle"
    assert all("upload rejected (AccessDenied)" in r["last_error"] and r["attempts"] == 1 for r in rows)
    runs = _runs(config)
    assert [r["status"] for r in runs] == ["failed", "failed"]
    assert all("upload rejected (AccessDenied)" in r["error_message"] for r in runs)
    log = _log_text(config)
    assert "upload rejected (AccessDenied); check the access key and the IAM policy for the bucket" in log
    assert "no further upload is tried this cycle" in log

    # Still rejected on the next cycle: the drain tries the oldest file,
    # stops, and the rest wait untouched (no attempt, no sleep) while the
    # cycle's own new files are spooled with the reason.
    fake_uploader.calls = []
    assert agent.main(argv) == 1
    assert [name for _, name in _uploads(fake_uploader)] == [rows[0]["object_name"]]
    again = _spool_rows(config)
    assert [r["attempts"] for r in again[: len(rows)]] == [2] + [1] * (len(rows) - 1)
    assert len(again) == 2 * len(rows) and all(r["attempts"] == 1 for r in again[len(rows):])
    assert all("upload rejected (AccessDenied)" in r["last_error"] for r in again)

    # Key fixed: everything drains, then the new files go up.
    fake_uploader.error = None
    fake_uploader.calls = []
    assert agent.main(argv) == 0
    assert _spool_rows(config) == [] and _spool_files(config) == []


def test_test_connection_probes_the_scoped_prefix(b1_env, dataset, tmp_path, agent, fake_uploader, capsys):
    config = _write_config(tmp_path, upload=True)
    assert agent.main(["--config", str(config), "--quiet", "test-connection"]) == 0
    out = capsys.readouterr().out
    assert f"upload: bucket agent-test-bucket, prefix raw/sap_b1/*/{SCOPE} reachable" in out
    assert fake_uploader.calls == [("probe", SCOPE)]


def test_a_missing_spool_file_is_a_lost_file_and_a_failed_cycle(b1_env, dataset, tmp_path, agent, fake_uploader):
    """The watermark already covers a spooled batch; a file that vanished
    is a permanent Bronze gap and must never pass as a clean cycle."""
    config = _write_config(tmp_path, upload=True)
    argv = ["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full"]
    fake_uploader.error = agent.UploadError("upload failed after 5 attempts: ConnectionError: down")
    assert agent.main(argv) == 1
    rows = _spool_rows(config)
    assert len(rows) >= 2
    gone = rows[0]
    Path(gone["path"]).unlink()

    fake_uploader.error = None
    fake_uploader.calls = []
    assert agent.main(argv) == 1, "a lost file fails the cycle even though every other upload worked"
    uploaded = [name for _, name in _uploads(fake_uploader)]
    assert gone["object_name"] not in uploaded
    assert all(r["object_name"] in uploaded for r in rows[1:])
    assert _spool_rows(config) == []
    lost = _spool_rows(config, "spool_lost")
    assert len(lost) == 1
    assert lost[0]["object_name"] == gone["object_name"] and lost[0]["run_id"] == gone["run_id"]
    assert lost[0]["reason"] == "spool file is missing" and lost[0]["quarantine_path"] is None and lost[0]["lost_at"]
    assert [r["status"] for r in _runs(config)] == ["failed", "success"], "the new run itself succeeded"
    log = _log_text(config)
    assert "spool file lost, its rows never reached S3 and the watermark already passed them" in log
    assert "spool: 1 file(s) lost" in log

    report = json.loads(_run(config, "status", "--json").stdout)
    assert report["spool_lost"] == 1 and report["spool_pending"] == 0
    assert report["lost_files"][0]["object_name"] == gone["object_name"]
    text = _run(config, "status").stdout
    assert "lost: 1" in text and f"LOST {gone['object_name']}" in text and "re-extract the table" in text

    # The record stays; a later cycle is not blocked by it.
    assert agent.main(argv) == 0
    assert len(_spool_rows(config, "spool_lost")) == 1


def test_a_truncated_spool_file_is_quarantined_and_never_uploaded(b1_env, dataset, tmp_path, agent, fake_uploader):
    config = _write_config(tmp_path, upload=True)
    argv = ["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full"]
    fake_uploader.error = agent.UploadError("upload failed after 5 attempts: ConnectionError: down")
    assert agent.main(argv) == 1
    rows = _spool_rows(config)
    assert len(rows) >= 2
    damaged = Path(rows[0]["path"])
    raw = damaged.read_bytes()
    damaged.write_bytes(raw[: len(raw) // 2])

    fake_uploader.error = None
    fake_uploader.calls = []
    assert agent.main(argv) == 1
    uploaded = [name for _, name in _uploads(fake_uploader)]
    assert rows[0]["object_name"] not in uploaded and all(r["object_name"] in uploaded for r in rows[1:])
    quarantine = tmp_path / "state" / "spool" / "quarantine"
    assert not damaged.exists() and (quarantine / damaged.name).is_file()
    assert (quarantine / damaged.name).read_bytes() == raw[: len(raw) // 2]
    lost = _spool_rows(config, "spool_lost")
    assert len(lost) == 1 and lost[0]["object_name"] == rows[0]["object_name"]
    assert lost[0]["reason"].startswith("unreadable parquet file (") and lost[0]["quarantine_path"] == str(quarantine / damaged.name)
    assert _spool_rows(config) == [] and _spool_files(config) == []
    assert json.loads(_run(config, "status", "--json").stdout)["spool_lost"] == 1


def test_an_uploaded_file_windows_will_not_let_us_delete_is_still_delivered(b1_env, dataset, tmp_path, agent, fake_uploader, monkeypatch):
    """S3 confirmed the upload: a PermissionError from an antivirus holding
    the file must not fail the run (the watermark has moved; a retry would
    duplicate the batch). The leftover is swept on the next drain."""
    config = _write_config(tmp_path, upload=True)
    argv = ["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full"]
    real_unlink = Path.unlink
    spool_dir = tmp_path / "state" / "spool"

    def locked_unlink(self, missing_ok=False):
        if self.parent == spool_dir and self.suffix == ".parquet":
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    assert agent.main(argv) == 0
    runs = _runs(config)
    assert runs[0]["status"] == "success" and not runs[0]["error_message"]
    assert _spool_rows(config) == [], "delivered: the ledger row is gone"
    leftovers = _spool_files(config)
    assert len(leftovers) == runs[0]["batches"] >= 1
    assert "uploaded file could not be removed yet, swept on the next run" in _log_text(config)

    monkeypatch.setattr(Path, "unlink", real_unlink)
    fake_uploader.calls = []
    assert agent.main(argv) == 0
    assert _spool_files(config) == []
    assert all(name != leftover.name for name, _ in _uploads(fake_uploader) for leftover in leftovers), "swept, not re-uploaded"
    assert _log_text(config).count("removed leftover spool file") == len(leftovers)
    assert [r["status"] for r in _runs(config)] == ["success", "success"]


# ── run lock ───────────────────────────────────────────────────────────────


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def test_the_run_lock_refuses_a_live_holder_takes_over_a_dead_one_and_is_released(agent, sleeper, tmp_path):
    lock = tmp_path / "state" / "agent.lock"
    lock.parent.mkdir()
    lock.write_text(f"{sleeper.pid} 2026-09-24T00:00:00Z\n", encoding="utf-8")
    with pytest.raises(agent.AgentError, match=rf"another agent run is in progress \(pid {sleeper.pid}\)"):
        with agent.RunLock(lock):
            pass
    assert lock.read_text(encoding="utf-8").startswith(f"{sleeper.pid} "), "a live holder's lock is untouched"

    sleeper.kill()
    sleeper.wait()
    with agent.RunLock(lock):
        text = lock.read_text(encoding="utf-8")
        assert text.split()[0] == str(os.getpid()) and len(text.split()) == 2, text
        assert sorted(p.name for p in lock.parent.iterdir()) == ["agent.lock"], "no staging file survives the claim"
    assert not lock.exists(), "released on exit"
    assert list(lock.parent.iterdir()) == []


def test_an_unreadable_lock_is_never_taken_over(agent, tmp_path):
    lock = tmp_path / "agent.lock"
    for content in ("", "   \n", "not-a-pid 2026-09-24T00:00:00Z\n", "-5\n", "0\n"):
        lock.write_text(content, encoding="utf-8")
        with pytest.raises(agent.AgentError, match="the run lock cannot be read .*delete it and run again"):
            with agent.RunLock(lock):
                pass
        assert lock.read_text(encoding="utf-8") == content, "left for the operator, exactly as found"


def test_a_second_run_exits_1_at_once_while_another_holds_the_lock(agent, sleeper, tmp_path, monkeypatch):
    for name, value in (("SAP_B1_DIALECT", "postgres"), ("SAP_B1_HOST", "source-under-test"), ("SAP_B1_PORT", "5432"),
                        ("SAP_B1_USER", "reader"), ("SAP_B1_PASSWORD", "reader-password-under-test"),
                        ("SAP_B1_DATABASE", "b1"), ("SAP_B1_COMPANIES", "mx_mfg=SCHEMA_UNDER_TEST")):
        monkeypatch.setenv(name, value)
    config = _write_config(tmp_path)
    lock = tmp_path / "state" / "agent.lock"
    lock.parent.mkdir()
    lock.write_text(f"{sleeper.pid} 2026-09-24T00:00:00Z\n", encoding="utf-8")
    started = datetime.now()
    code = agent.main(["--config", str(config), "--quiet", "extract", "--entity", "CINF", "--mode", "full",
                       "--output-dir", str(tmp_path / "out")])
    assert code == 1 and (datetime.now() - started) < timedelta(seconds=5), "no waiting"
    assert f"another agent run is in progress (pid {sleeper.pid})" in _log_text(config)
    assert _runs(config) == [], "nothing ran"
    assert lock.read_text(encoding="utf-8").startswith(f"{sleeper.pid} ")


# ── modes: incremental after an edit, a date range, a snapshot table ───────


def test_a_date_range_is_a_historical_read_that_stores_no_watermark(b1_env, dataset, tmp_path):
    import pyarrow.parquet as pq

    doc_date = _col("OINV", "DocDate")
    start = dataset.as_of - timedelta(days=120)
    end = dataset.as_of - timedelta(days=91)

    def _day(value):
        return value.date() if isinstance(value, datetime) else value

    expected = {
        c.alias: {row[_col("OINV", "DocEntry")] for row in dataset.tables[c.alias]["OINV"] if start <= _day(row[doc_date]) <= end}
        for c in dataset.companies
    }
    assert all(expected.values()), "the range must hold documents in every company"
    outside = sum(1 for c in dataset.companies for row in dataset.tables[c.alias]["OINV"] if not start <= _day(row[doc_date]) <= end)
    assert outside > 0

    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "extract", "--entity", "OINV", "--from-date", start.isoformat(), "--to-date", end.isoformat(),
                "--output-dir", str(out))
    assert proc.returncode == 0, proc.stderr
    runs = _runs(config)
    assert len(runs) == 1 and runs[0]["run_type"] == "historical" and runs[0]["status"] == "success"
    assert _watermarks(config) == {}, "a historical read never moves a watermark"
    seen = defaultdict(set)
    for path in out.rglob("*.parquet"):
        for row in pq.read_table(path).to_pylist():
            assert row["_load_type"] == "historical"
            assert start <= row["DocDate"].date() <= end, row["DocDate"]
            seen[row["_company"]].add(row["DocEntry"])
    assert dict(seen) == expected
    assert runs[0]["records_extracted"] == sum(len(v) for v in expected.values())

    mixed = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--from-date", start.isoformat(),
                 "--output-dir", str(tmp_path / "mixed"))
    assert mixed.returncode == 2 and "--mode cannot be combined with --from-date/--to-date" in mixed.stderr + _log_text(config)


def test_a_snapshot_table_asked_incrementally_is_read_whole(b1_env, dataset, tmp_path):
    import pyarrow.parquet as pq

    from app.services import b1_queries as q

    config = _write_config(tmp_path)
    out = tmp_path / "out"
    proc = _run(config, "extract", "--entity", "OITW", "--mode", "incremental", "--output-dir", str(out))
    assert proc.returncode == 0, proc.stderr
    runs = _runs(config)
    assert len(runs) == 1 and runs[0]["run_type"] == "full" and runs[0]["status"] == "success"
    assert "OITW: incremental requested but the table has no update stamp; read whole (full)" in _log_text(config)
    files = sorted(out.rglob("OITW.parquet"))
    assert files and len(files) == runs[0]["batches"]
    declared = q.arrow_schema(q.plan_from_config(_entity_config("OITW")))
    total = 0
    for path in files:
        table = pq.read_table(path)
        assert table.schema.equals(declared, check_metadata=True)
        assert all(row["_load_type"] == "full" and row["_watermark_value"] is None for row in table.to_pylist())
        total += table.num_rows
    assert total == runs[0]["records_extracted"] == sum(len(dataset.tables[c.alias]["OITW"]) for c in dataset.companies)
    assert not any(k.startswith("OITW@") for k in _watermarks(config)), "a snapshot records no watermark"


# ── entity selection ───────────────────────────────────────────────────────


def _config_from(agent, tmp_path: Path, agent_lines: str):
    return agent.load_config(_write_config(tmp_path, extra=agent_lines), env={})


def test_entities_and_exclude_shape_extract_all_and_an_explicit_entity_wins(agent, tmp_path, caplog):
    catalogue = agent.load_catalogue()
    names = [e["entity"] for e in catalogue]
    log = logging.getLogger("selection-under-test")

    restricted = _config_from(agent, tmp_path / "a", 'entities = ["CINF", "OITW", "OINV"]\nexclude = ["OITW"]\n')
    assert [e["entity"] for e in agent.select_entities(catalogue, restricted)] == ["CINF", "OINV"]

    with caplog.at_level(logging.INFO, logger=log.name):
        chosen = agent.select_entities(catalogue, restricted, only=["OITW"], log=log)
    assert [e["entity"] for e in chosen] == ["OITW"]
    assert "--entity OITW overrides [agent] exclude for this run" in caplog.text

    as_text = _config_from(agent, tmp_path / "b", 'exclude = "OINV,INV1"\n')
    assert as_text.exclude == ["OINV", "INV1"]
    assert [e["entity"] for e in agent.select_entities(catalogue, as_text)] == [n for n in names if n not in ("OINV", "INV1")]
    assert [e["entity"] for e in agent.select_entities(catalogue, as_text, only=["INV1", "OINV"])] == ["INV1", "OINV"]

    everything = _config_from(agent, tmp_path / "c", "entities = []\nexclude = []\n")
    assert [e["entity"] for e in agent.select_entities(catalogue, everything)] == names

    for lines in ('entities = ["CINF", "NOPE"]\n', 'exclude = ["NOPE"]\n', 'exclude = "OINV,NOPE"\n'):
        broken = _config_from(agent, tmp_path / "d", lines)
        with pytest.raises(agent.ConfigError, match="unknown entities .*NOPE"):
            agent.select_entities(catalogue, broken)
        with pytest.raises(agent.ConfigError, match="NOPE"):
            agent.select_entities(catalogue, broken, only=["CINF"])
    with pytest.raises(agent.ConfigError, match="NOPE"):
        agent.select_entities(catalogue, everything, only=["NOPE"])


def test_an_unknown_name_in_entities_or_exclude_is_exit_2_before_any_connection(tmp_path):
    env = {k: v for k, v in _clean_env().items() if not k.startswith("SAP_B1_")}
    for lines in ('entities = ["NOPE"]\n', 'exclude = "OINV,NOPE"\n'):
        config = _write_config(tmp_path, extra=lines)
        proc = _run(config, "extract-all", "--output-dir", str(tmp_path / "out"), env=env)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "unknown entities (not in entities.yaml): NOPE" in _log_text(config)
        proc = _run(config, "extract", "--entity", "CINF", "--output-dir", str(tmp_path / "out"), env=env)
        assert proc.returncode == 2 and "NOPE" in _log_text(config)
    assert not (tmp_path / "state" / "agent-state.sqlite").exists(), "refused before any state or connection"


def test_extract_all_honours_entities_and_exclude_against_the_source(b1_env, dataset, tmp_path):
    config = _write_config(tmp_path, extra='entities = ["OITW", "CINF"]\nexclude = "OITW"\n')
    proc = _run(config, "extract-all", "--mode", "full", "--skip-intercompany", "--output-dir", str(tmp_path / "all"))
    assert proc.returncode == 0, proc.stderr
    assert [r["entity_name"] for r in _runs(config)] == ["CINF"]
    assert {p.name for p in (tmp_path / "all").rglob("*.parquet")} == {"CINF.parquet"}

    proc = _run(config, "extract", "--entity", "OITW", "--mode", "full", "--output-dir", str(tmp_path / "one"))
    assert proc.returncode == 0, proc.stderr
    assert [r["entity_name"] for r in _runs(config)] == ["CINF", "OITW"]
    assert "--entity OITW overrides [agent] exclude for this run" in _log_text(config)


# ── installed layout ───────────────────────────────────────────────────────


def _module_level(tree: ast.Module):
    """Statements that run on import: the module body, looking into ``if``
    and ``try`` blocks but never into a function or class body (the
    cartridge modules import their platform-only helpers lazily, inside
    functions the agent never calls; the installed-layout test proves it)."""
    todo = list(tree.body)
    while todo:
        node = todo.pop()
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody", "handlers"):
                for child in getattr(node, field, []) or []:
                    todo.extend(child.body if isinstance(child, ast.ExceptHandler) else [child])


def _transitive_app_modules(start: Path) -> set[str]:
    """Every ``app.*`` module ``start`` imports at import time, directly or
    through the modules it imports."""
    modules: set[str] = set()
    todo, seen = [start], set()
    while todo:
        file = todo.pop()
        if file in seen:
            continue
        seen.add(file)
        for node in _module_level(ast.parse(file.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app":
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}"
                    names.append(candidate if (CARTRIDGE_ROOT / (candidate.replace(".", "/") + ".py")).is_file() else node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names if alias.name.split(".")[0] == "app")
            for name in names:
                path = CARTRIDGE_ROOT / (name.replace(".", "/") + ".py")
                if path.is_file():
                    modules.add(name)
                    todo.append(path)
    return modules


def test_install_copies_exactly_the_modules_the_agent_imports(agent):
    """install.ps1, agent.py's manifest and the transitive ``app.*`` imports
    of agent.py must agree, or the installed agent dies on an ImportError."""
    modules = _transitive_app_modules(AGENT)
    assert "app.services.intercompany_mapping" in modules
    expected = {module.replace(".", "/") + ".py" for module in modules}
    for module in modules:
        parts = module.split(".")
        expected.update("/".join(parts[:depth]) + "/__init__.py" for depth in range(1, len(parts)))
    expected.add("app/config/entities.yaml")
    assert set(agent.CARTRIDGE_FILES) == expected, "agent.CARTRIDGE_FILES is out of step with agent.py's imports"
    assert set(_ps_array("cartridgeFiles")) == expected, "install.ps1 $cartridgeFiles is out of step"
    assert "agent.py" in _ps_array("agentFiles") and "requirements.txt" in _ps_array("agentFiles")


def _install_like_install_ps1(tmp_path: Path) -> Path:
    """The tree install.ps1 produces: <InstallRoot>/windows-agent/* and
    <InstallRoot>/app/*, copied from the very lists the script holds."""
    root = tmp_path / "InstallRoot"
    for relative in _ps_array("agentFiles"):
        target = root / "windows-agent" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(AGENT_DIR / relative, target)
    for relative in _ps_array("cartridgeFiles"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(CARTRIDGE_ROOT / relative, target)
    return root / "windows-agent" / "agent.py"


def test_the_installed_layout_runs_against_the_source_without_the_repository(b1_env, dataset, tmp_path):
    installed = _install_like_install_ps1(tmp_path)
    env = {k: v for k, v in _clean_env().items() if k != "PYTHONPATH"}
    config = _write_config(tmp_path / "data")

    probe = (
        "import sys, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('installed_agent', {str(installed)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['installed_agent'] = m; spec.loader.exec_module(m)\n"
        "print('ROOT=' + str(m.CARTRIDGE_ROOT))\n"
        "print('READER=' + sys.modules['app.services.b1_reader'].__file__)\n"
        "print('MAPPING=' + sys.modules['app.services.intercompany_mapping'].__file__)\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], env=env, cwd=str(tmp_path), capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    root = str(installed.parents[1])
    assert f"ROOT={root}" in proc.stdout and f"READER={root}/" in proc.stdout and f"MAPPING={root}/" in proc.stdout
    assert str(CARTRIDGE_ROOT) not in proc.stdout

    proc = _run(config, "test-connection", "--output-dir", str(tmp_path / "out"), env=env, agent_path=installed)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr and "reachable, Business One version" in proc.stdout
    proc = _run(config, "extract", "--entity", "CINF", "--mode", "full", "--output-dir", str(tmp_path / "out"), env=env, agent_path=installed)
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in (tmp_path / "out").rglob("*.parquet")) == ["CINF.parquet"] * len(dataset.companies)
    proc = _run(config, "refresh-intercompany", "--output-dir", str(tmp_path / "ic"), env=env, agent_path=installed)
    assert proc.returncode == 0, proc.stderr
    assert [p.name for p in (tmp_path / "ic").rglob("*.parquet")] == ["IntercompanyPartners.parquet"]

    # An incomplete copy is refused up front with the missing file named.
    (installed.parents[1] / "app" / "services" / "intercompany_mapping.py").unlink()
    proc = _run(config, "status", env=env, agent_path=installed)
    assert proc.returncode == 1
    assert "incomplete, missing app/services/intercompany_mapping.py; run install.ps1 again" in proc.stderr


# ── incremental after a real edit in the source (last: it edits the fake) ──


def test_an_incremental_run_picks_up_a_row_edited_in_the_source(b1_env, dataset, tmp_path):
    import pyarrow.parquet as pq

    config = _write_config(tmp_path)
    proc = _run(config, "extract", "--entity", "OINV", "--mode", "full", "--output-dir", str(tmp_path / "first"))
    assert proc.returncode == 0, proc.stderr
    before = _watermarks(config)

    company = dataset.companies[0]
    doc_entry = dataset.tables[company.alias]["OINV"][0][_col("OINV", "DocEntry")]
    dsn = b1_env["dsn"]
    table = f'"{company.schema}"."OINV"'
    original = _pg(dsn, f'SELECT "UpdateDate", "UpdateTS" FROM {table} WHERE "DocEntry" = %s', (doc_entry,), fetch=True)[0]
    later = datetime.combine(dataset.as_of + timedelta(days=3), datetime.min.time())
    _pg(dsn, f'UPDATE {table} SET "UpdateDate" = %s, "UpdateTS" = %s WHERE "DocEntry" = %s', (later, 101500, doc_entry))
    try:
        second = tmp_path / "second"
        proc = _run(config, "extract", "--entity", "OINV", "--mode", "incremental", "--output-dir", str(second))
        assert proc.returncode == 0, proc.stderr
        rows = [row for path in second.rglob("*.parquet") for row in pq.read_table(path).to_pylist()]
        edited = [row for row in rows if row["_company"] == company.alias and row["DocEntry"] == doc_entry]
        assert len(edited) == 1, "the edited invoice is read exactly once"
        assert edited[0]["_source_updated_at"] == later.replace(hour=10, minute=15).strftime(_STAMP)
        assert edited[0]["_load_type"] == "incremental"
        for row in rows:
            boundary = datetime.strptime(before[f"OINV@{row['_company']}"], _STAMP) - timedelta(minutes=5)
            assert datetime.strptime(row["_source_updated_at"], _STAMP) >= boundary, "only rows past the stored mark"
        runs = _runs(config)
        assert [r["run_type"] for r in runs] == ["full", "incremental"] and runs[-1]["status"] == "success"
        assert runs[-1]["records_extracted"] == len(rows) >= 1
        after = _watermarks(config)
        assert all(after[key] >= before[key] for key in before)
    finally:
        _pg(dsn, f'UPDATE {table} SET "UpdateDate" = %s, "UpdateTS" = %s WHERE "DocEntry" = %s', (original[0], original[1], doc_entry))

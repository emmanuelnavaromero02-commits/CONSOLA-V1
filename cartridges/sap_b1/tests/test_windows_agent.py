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

import importlib
import importlib.util
import json
import os
import re
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
# Nothing of the platform reaches the agent process.
_PLATFORM_PREFIXES = (
    "DATABASE_URL", "MINIO_", "LAKEHOUSE_", "FIELD_ENCRYPTION_KEY", "INTERNAL_API_KEY",
    "SECURITY_CONTEXT", "AWS_", "OMEGA_", "APP_ENV", "S3_BUCKET", "GCS_",
)
_STAMP = "%Y-%m-%dT%H:%M:%S"


# ── helpers ────────────────────────────────────────────────────────────────


def _entity_config(entity: str) -> dict:
    with ENTITIES.open(encoding="utf-8") as handle:
        return next(dict(e) for e in yaml.safe_load(handle)["entities"] if e["entity"] == entity)


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "agent.toml"
    config.write_text(
        "[agent]\n"
        f'tenant_id = "{TENANT}"\n'
        f'workspace_id = "{WORKSPACE}"\n'
        "state_dir = 'state'\n",
        encoding="utf-8",
    )
    return config


def _clean_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_PLATFORM_PREFIXES)}
    env.update(overrides)
    assert "DATABASE_URL" not in env and "FIELD_ENCRYPTION_KEY" not in env
    return env


def _run(config: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AGENT), "--config", str(config), *args],
        env=env or _clean_env(),
        cwd=str(config.parent),
        capture_output=True,
        text=True,
        check=False,
    )


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
    for text in (log, proc.stderr, proc.stdout, wrong.stderr, wrong.stdout, json.dumps(_runs(config))):
        assert b1_env["password"] not in text
        assert "not-the-password-either" not in text
        assert b1_env["host"] not in text
        assert b1_env["user"] not in text
        assert all(c.schema not in text for c in dataset.companies)
    assert "failed" in log


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
    text = _run(config, "status").stdout
    assert "OINV" in text and b1_env["host"] not in text and b1_env["password"] not in text


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

    config.write_text('[agent]\ntenant_id = "t"\nworkspace_id = "w"\nstate_dir = \'state\'\n', encoding="utf-8")
    proc = _run(config, "extract", "--entity", "OINV", "--output-dir", str(tmp_path / "out"), env=env)
    assert proc.returncode == 2 and "SAP_B1_HOST" in proc.stderr and "SAP_B1_PASSWORD" in proc.stderr

    proc = _run(config, "extract", "--entity", "NOPE", "--output-dir", str(tmp_path / "out"), env=env)
    assert proc.returncode == 2 and "NOPE" in proc.stderr


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
            assert statement["Condition"] == {"StringLike": {"s3:prefix": ["raw/sap_b1/*"]}}
        else:
            assert resources == ["arn:aws:s3:::<BUCKET>/raw/sap_b1/*"]
            assert "Condition" not in statement or statement["Condition"] == {}
    text = json.dumps(policy)
    assert "GetObject" not in text and "DeleteObject" not in text and '"*"' not in text and "s3:*" not in text


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

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tests.conftest import load_cartridge_app


ROOT = Path(__file__).resolve().parents[1]
REPLICON = ROOT / "cartridges/replicon"
PUBLIC_ERROR = "DuckDB remote source unavailable"


@pytest.fixture(autouse=True)
def _restore_process_import_state():
    original_env = dict(os.environ)
    original_path = list(sys.path)
    original_app_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    yield
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    sys.modules.update(original_app_modules)
    sys.path[:] = original_path
    for name in set(os.environ) - set(original_env):
        os.environ.pop(name)
    os.environ.update(original_env)


def _requirement(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().lower().startswith("duckdb")
    ]


def _runtime_env(home: Path, sql: str) -> dict[str, str]:
    return {
        **os.environ,
        "HOME": str(home),
        "PYTHONPATH": f"{REPLICON}:{ROOT}",
        "MINIO_ACCESS_KEY": "test-access",
        "MINIO_SECRET_KEY": "test-secret",
        "PG_USER": "test-user",
        "PG_PASSWORD": "test-password",
        "TEST_SQL": sql,
    }


def _run_runtime(home: Path, sql: str) -> dict:
    home.mkdir()
    script = """
import json, os
from app.services.duckdb_service import run_kb_sql
try:
    frame = run_kb_sql(os.environ["TEST_SQL"])
except Exception as exc:
    result = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
else:
    result = {"status": "ok", "rows": frame.to_dict(orient="records")}
print(json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_runtime_env(home, sql),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_replicon_and_tests_pin_the_same_duckdb_version() -> None:
    expected = ["duckdb==1.2.2"]
    assert _requirement(REPLICON / "requirements.txt") == expected
    assert _requirement(ROOT / "tests/requirements.txt") == expected


def test_local_parquet_is_denied_hermetically_and_deterministically(tmp_path: Path) -> None:
    parquet = tmp_path / "observed.parquet"
    pd.DataFrame({"value": [7]}).to_parquet(parquet, index=False)
    sql = f"SELECT value FROM read_parquet('{parquet}')"
    results = []
    for index in range(2):
        home = tmp_path / f"home-{index}"
        results.append(_run_runtime(home, sql))
        assert not (home / ".duckdb/extensions").exists()
    assert results[0] == results[1]
    assert results[0]["status"] == "error"
    assert "disabled by configuration" in results[0]["message"]


def test_connection_disables_extension_install_and_skips_s3_for_local(
    monkeypatch,
) -> None:
    load_cartridge_app("replicon")
    from app.services import duckdb_service

    calls: list[str] = []

    class Connection:
        def execute(self, sql: str, *_params):
            calls.append(sql)
            return self

        def close(self):
            calls.append("CLOSE")

    configs: list[dict] = []

    def connect(*, config):
        configs.append(config)
        return Connection()

    monkeypatch.setattr(duckdb_service.duckdb, "connect", connect)
    conn = duckdb_service._get_duckdb_connection("SELECT 1")
    assert configs == [
        {
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        }
    ]
    assert "LOAD httpfs;" not in calls
    assert calls[0].startswith("SET allowed_directories=['s3://")
    assert calls[1:] == ["SET enable_external_access=false;", "SET lock_configuration=true;"]
    conn.close()


def test_remote_missing_httpfs_fails_closed_before_query(monkeypatch) -> None:
    load_cartridge_app("replicon")
    from app.services import duckdb_service

    remote_sql = "SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/x.parquet')"
    calls: list[str] = []

    class Connection:
        def execute(self, sql: str, *_params):
            calls.append(sql)
            if sql == "LOAD httpfs;":
                raise duckdb_service.duckdb.IOException(
                    "/private/home/.duckdb/extensions/httpfs secret s3://target"
                )
            raise AssertionError("resolved SQL must not execute")

        def close(self):
            calls.append("CLOSE")

    monkeypatch.setattr(
        duckdb_service.duckdb,
        "connect",
        lambda *, config: Connection(),
    )
    with pytest.raises(duckdb_service.DuckDBHTTPFSUnavailable) as exc:
        duckdb_service._get_duckdb_connection(remote_sql)
    assert str(exc.value) == PUBLIC_ERROR
    assert repr(exc.value.__cause__) == "None"
    assert calls == ["LOAD httpfs;", "CLOSE"]
    assert "s3://" not in str(exc.value)
    assert ".duckdb" not in str(exc.value)


def test_httpfs_mode_comes_only_from_resolved_reader_sql(monkeypatch) -> None:
    load_cartridge_app("replicon")
    from app.services import duckdb_service

    calls: list[str] = []

    class Connection:
        def execute(self, sql: str, *_params):
            calls.append(sql)
            return self

        def close(self):
            return None

    monkeypatch.setattr(
        duckdb_service.duckdb,
        "connect",
        lambda *, config: Connection(),
    )
    duckdb_service._get_duckdb_connection("SELECT 's3://not-a-reader'").close()
    assert "LOAD httpfs;" not in calls
    calls.clear()
    duckdb_service._get_duckdb_connection(
        "SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/x.parquet')"
    ).close()
    assert calls[0] == "LOAD httpfs;"


def test_remote_process_without_extension_fails_sanitized(tmp_path: Path) -> None:
    home = tmp_path / "remote-home"
    result = _run_runtime(
        home,
        "SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/x.parquet')",
    )
    assert result == {
        "status": "error",
        "type": "DuckDBHTTPFSUnavailable",
        "message": PUBLIC_ERROR,
    }
    assert not (home / ".duckdb/extensions").exists()


def test_httpfs_failure_cannot_publish_materialization(monkeypatch) -> None:
    from tests.test_operational_truth_data_kb_runtime import _load_replicon_runtime

    _reconciliation, _catalog, service = _load_replicon_runtime(monkeypatch)
    config = {
        "id": "custom_kb",
        "kb_id": "custom_kb",
        "sql": "SELECT * FROM read_parquet("
        "'s3://b/raw/replicon/TimeEntry/load_date=*/*.parquet')",
        "output_path": "gold/replicon/custom",
        "pg_table": "custom_kb",
    }
    run = SimpleNamespace(run_id="run-a")
    calls = {"parquet": 0, "gold": 0, "finish": 0, "fail": 0}
    monkeypatch.setattr(service, "get_kb_config", lambda _kb: config)
    monkeypatch.setattr(service, "blocked_kb_runtime_result", lambda _cfg: None)
    monkeypatch.setattr(service, "_create_kb_run", lambda *_a: run)
    monkeypatch.setattr(
        service,
        "run_kb_sql",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError(PUBLIC_ERROR)),
    )
    monkeypatch.setattr(
        service,
        "write_kb_parquet",
        lambda *_a, **_k: calls.__setitem__("parquet", 1),
    )
    monkeypatch.setattr(
        service,
        "write_kb_to_postgres",
        lambda *_a, **_k: calls.__setitem__("gold", 1),
    )
    monkeypatch.setattr(
        service,
        "_finish_kb_run",
        lambda *_a, **_k: calls.__setitem__("finish", 1),
    )
    monkeypatch.setattr(
        service,
        "_fail_kb_run",
        lambda *_a, **_k: calls.__setitem__("fail", 1),
    )
    result = service.run_knowledge_bit("custom_kb", {})
    assert result == {
        "run_id": "run-a",
        "kb_id": "custom_kb",
        "status": "failed",
        "error": PUBLIC_ERROR,
    }
    assert calls == {"parquet": 0, "gold": 0, "finish": 0, "fail": 1}


def test_control_room_gate_builds_and_smokes_real_replicon_image() -> None:
    workflow = (ROOT / ".github/workflows/control-room-postgres-rls.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts/ci_replicon_minio_smoke.sh").read_text(encoding="utf-8")
    contract = workflow + script
    required = (
        "docker build cartridges/replicon",
        "--network none",
        "--read-only",
        "--user appuser",
        "duckdb.__version__",
        "_get_duckdb_connection",
        "s3://ci-bucket/raw/replicon/TimeEntry",
        "autoinstall_known_extensions",
        "autoload_known_extensions",
    )
    assert all(token in contract for token in required)
    smoke = contract.split("Build and smoke Replicon DuckDB/httpfs image", 1)[1]
    assert "INSTALL httpfs" not in smoke

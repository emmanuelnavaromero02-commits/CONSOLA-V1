from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DAG = REPO_ROOT / "airflow" / "dags" / "file_ingest.py"


def _load_dag_module(monkeypatch):
    airflow = types.ModuleType("airflow")
    decorators = types.ModuleType("airflow.decorators")
    models = types.ModuleType("airflow.models")

    def dag(**_kwargs):
        return lambda function: function

    def task(function):
        def deferred(*_args, **_kwargs):
            return {"task": function.__name__}

        return deferred

    class ForbiddenVariable:
        @staticmethod
        def get(*_args, **_kwargs):
            raise AssertionError("GCS runtime must not read local MinIO Variables")

    decorators.dag = dag
    decorators.task = task
    models.Variable = ForbiddenVariable
    runtime_context = types.ModuleType("runtime_security_context")
    runtime_context.build_pipeline_run_context = lambda value: value
    refresh = types.ModuleType("dataset_refresh_admission")
    refresh.build_dataset_refresh_trigger = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.models", models)
    monkeypatch.setitem(sys.modules, "runtime_security_context", runtime_context)
    monkeypatch.setitem(sys.modules, "dataset_refresh_admission", refresh)

    spec = importlib.util.spec_from_file_location("file_ingest_gcs_contract", DAG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_no_prefix_wipe_and_per_file_objects():
    src = DAG.read_text(encoding="utf-8")
    ejecutable = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "Wipe day's prefix" not in src, "el barrido del día está prohibido"
    assert (
        "for obj in client.list_objects(bucket, prefix=out_pref, recursive=True):"
        not in ejecutable
    ), "prohibido listar el prefijo del día para borrarlo"
    assert 'data-{digest}.parquet' in src, "objeto por archivo fuente"
    assert 'hashlib.sha256(f["name"].encode' in src, "nombrado por hash del nombre"
    assert 'legacy_key = f"{out_pref}data.parquet"' in src
    assert ejecutable.count("client.remove_object(bucket, legacy_key)") == 1
    assert ejecutable.count("client.remove_object(") == 2


def test_same_filename_same_key_new_filename_new_key():
    def key(name: str) -> str:
        return "data-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12] + ".parquet"

    assert key("ventas_agosto.csv") == key("ventas_agosto.csv")
    assert key("ventas_agosto.csv") != key("ventas_septiembre.csv")


def test_reader_unions_per_file_objects(tmp_path):
    con = duckdb.connect()
    day = tmp_path / "raw" / "files" / "ventas" / "load_date=2026-08-19"
    day.mkdir(parents=True)
    con.execute(
        "CREATE TABLE a AS SELECT 'A' AS _source_file, 10 AS monto UNION ALL "
        "SELECT 'A', 20"
    )
    con.execute(f"COPY a TO '{(day / 'data-aaaaaaaaaaaa.parquet').as_posix()}' (FORMAT PARQUET)")
    con.execute(
        "CREATE TABLE b AS SELECT 'B' AS _source_file, 99 AS monto"
    )
    con.execute(f"COPY b TO '{(day / 'data-bbbbbbbbbbbb.parquet').as_posix()}' (FORMAT PARQUET)")
    total, fuentes = con.execute(
        f"""SELECT COUNT(*), COUNT(DISTINCT _source_file)
            FROM read_parquet('{(tmp_path / '**/*.parquet').as_posix()}',
                              hive_partitioning=true, union_by_name=true)"""
    ).fetchone()
    assert total == 3, "las filas de la mañana sobreviven a la tarde"
    assert fuentes == 2


def test_gcs_runtime_uses_only_complete_gcs_hmac_pair(monkeypatch):
    module = _load_dag_module(monkeypatch)
    for key in (
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.setenv(key, f"STALE-{key}")
    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("LAKEHOUSE_ENDPOINT", "storage.googleapis.com")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-access")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setenv("GCS_BUCKET", "omega-bronze")

    captured: dict = {}

    class FakeMinio:
        def __init__(self, endpoint, **kwargs):
            captured.update(endpoint=endpoint, **kwargs)

    minio = types.ModuleType("minio")
    minio.Minio = FakeMinio
    monkeypatch.setitem(sys.modules, "minio", minio)

    cfg = module._minio_cfg()
    module._minio_client()

    assert cfg == {
        "endpoint": "storage.googleapis.com",
        "access_key": "gcs-access",
        "secret_key": "gcs-secret",
        "bucket": "omega-bronze",
        "secure": True,
        "region": "auto",
    }
    assert captured == {
        "endpoint": "storage.googleapis.com",
        "access_key": "gcs-access",
        "secret_key": "gcs-secret",
        "secure": True,
        "region": "auto",
    }

    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "")
    with pytest.raises(RuntimeError, match="complete GCS lakehouse credentials"):
        module._minio_cfg()

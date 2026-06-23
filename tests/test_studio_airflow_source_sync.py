from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def execute(self, sql: str, args: tuple | None = None) -> None:
        self.executed.append((sql, args or ()))

    def fetchone(self):
        return ("STALE_DB_SOURCE", None)


class _Conn:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_obj = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1


def _load_pipeline(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(REPO / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "pg-password")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin-password")
    return importlib.import_module("app.tools.pipeline")


def test_dag_get_source_prefers_airflow_visible_file_over_stale_db(monkeypatch, tmp_path):
    pipeline = _load_pipeline(monkeypatch)
    monkeypatch.setattr(pipeline.settings, "airflow_dags_path", str(tmp_path))
    dag_path = tmp_path / "sap_successfactors" / "sap_successfactors_extract.py"
    dag_path.parent.mkdir(parents=True)
    dag_path.write_text("LIVE_AIRFLOW_SOURCE", encoding="utf-8")
    cursor = _Cursor()
    conn = _Conn(cursor)
    monkeypatch.setattr(pipeline, "_conn", lambda: conn)

    result = pipeline.dag_get_source("sap_successfactors", "sap_successfactors_extract")

    assert result["found"] is True
    assert result["source"] == "airflow_cartridge"
    assert result["source_code"] == "LIVE_AIRFLOW_SOURCE"
    assert conn.commits == 1
    assert any("INSERT INTO cartridge_dags" in sql for sql, _args in cursor.executed)


def test_dag_save_source_refuses_db_only_update_in_production(monkeypatch, tmp_path):
    pipeline = _load_pipeline(monkeypatch)
    monkeypatch.setattr(pipeline.settings, "airflow_dags_path", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_RCE_TOOLS", raising=False)
    dag_path = tmp_path / "sap_successfactors" / "sap_successfactors_extract.py"
    dag_path.parent.mkdir(parents=True)
    dag_path.write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "_conn",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )

    result = pipeline.dag_save_source("sap_successfactors", "sap_successfactors_extract", "NEW")

    assert result["saved"] is False
    assert "refuses DB-only updates" in result["error"]
    assert dag_path.read_text(encoding="utf-8") == "OLD"


def test_cartridge_export_documents_disk_as_dag_source_of_truth():
    source = (REPO / "console" / "app" / "services" / "cartridge_service.py").read_text(encoding="utf-8")

    assert "disk is canonical" in source
    assert "/registry/cartridges/{cartridge_id}/dags" in source
    assert "/opt/airflow/dags" in source


def test_pipeline_run_save_reuses_studio_trigger_row_by_airflow_run_id():
    source = (REPO / "mcp-infra" / "app" / "tools" / "pipeline.py").read_text(encoding="utf-8")
    save_section = source.split("def pipeline_run_save", 1)[1].split("# ── DAG source storage", 1)[0]

    assert "canonical_run_id" in save_section
    assert "AND airflow_dag_run_id = %s" in save_section
    assert "CASE WHEN run_id = %s THEN 0 ELSE 1 END" in save_section
    assert "run_id = canonical_run_id or run_id" in save_section

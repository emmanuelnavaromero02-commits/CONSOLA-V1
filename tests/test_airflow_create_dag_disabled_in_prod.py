"""Sprint v1.32 — airflow_create_dag is RCE and must be disabled in prod."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_airflow_tools(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "pg-password")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin-password")
    return importlib.import_module("app.tools.airflow")


@pytest.mark.asyncio
async def test_airflow_create_dag_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="disabled outside development"):
        await airflow.airflow_create_dag(
            dag_id="test_rce_blocked",
            code="print('this must not be written')\n",
        )


@pytest.mark.asyncio
async def test_airflow_delete_dag_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="deleting DAGs is a destructive operation"):
        await airflow.airflow_delete_dag(dag_id="test_rce_blocked")


@pytest.mark.asyncio
async def test_airflow_set_variable_disabled_in_production(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match="Airflow Variables are persistent runtime configuration"):
        await airflow.airflow_set_variable(key="danger", value="blocked")


@pytest.mark.asyncio
async def test_airflow_create_dag_still_available_in_development(monkeypatch, tmp_path):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(airflow.settings, "airflow_dags_path", str(tmp_path))

    result = await airflow.airflow_create_dag(
        dag_id="test_dev_dag",
        code="print('dev only')\n",
    )

    assert result["dag_id"] == "test_dev_dag"
    assert (tmp_path / "test_dev_dag.py").read_text() == "print('dev only')\n"

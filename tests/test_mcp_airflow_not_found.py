from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _load_airflow_tools(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_URL", "http://airflow:8080/airflow")
    monkeypatch.setenv("AIRFLOW_USER", "admin")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "admin")
    try:
        return importlib.import_module("app.tools.airflow")
    finally:
        try:
            sys.path.remove(str(root / "mcp-infra"))
        except ValueError:
            pass


class FakeResponse:
    status_code = 404
    text = ""

    def raise_for_status(self):
        raise AssertionError("404 should be handled without raise_for_status")

    def json(self):
        return {}


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, *args, **kwargs):
        return FakeResponse()


@pytest.mark.anyio
async def test_airflow_get_run_status_404_is_not_a_500(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setattr(airflow, "_client", lambda: FakeClient())

    result = await airflow.airflow_get_run_status("sap_successfactors_extract", "manual__missing")

    assert result == {
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "manual__missing",
        "found": False,
        "state": "not_found",
        "start_date": None,
        "end_date": None,
    }


@pytest.mark.anyio
async def test_airflow_list_task_instances_404_returns_empty_tasks(monkeypatch):
    airflow = _load_airflow_tools(monkeypatch)
    monkeypatch.setattr(airflow, "_client", lambda: FakeClient())

    result = await airflow.airflow_list_task_instances("sap_successfactors_extract", "manual__missing")

    assert result == {
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "manual__missing",
        "found": False,
        "tasks": [],
    }

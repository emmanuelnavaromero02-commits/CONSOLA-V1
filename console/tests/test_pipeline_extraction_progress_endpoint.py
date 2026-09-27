from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
READER = {
    "id": 3,
    "email": "reader@example.com",
    "role": "analyst",
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "allowed_cartridges": ["sap_successfactors"],
}
PATH = "/api/pipelines/extraction-progress"


def _module():
    return import_module("app.routers.pipeline_operations")


def _client(user):
    application = FastAPI()

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    application.include_router(_module().router)
    return TestClient(application)


def _rows():
    now = datetime.now(timezone.utc)
    return [
        {
            "run_id": "manual__all",
            "airflow_dag_run_id": "manual__all",
            "dag_id": "sap_successfactors_extract_all",
            "entity": "__extract_all__",
            "status": "running",
            "started_at": now - timedelta(minutes=3),
            "finished_at": None,
            "record_count": None,
            "error_message": None,
            "extra": {"airflow_observation": {"state": "running", "observed_at": (now - timedelta(seconds=30)).isoformat()}},
        },
        {
            "run_id": "manual__all:User",
            "airflow_dag_run_id": "manual__all",
            "dag_id": "sap_successfactors_extract_all",
            "entity": "User",
            "status": "success",
            "started_at": now - timedelta(minutes=3),
            "finished_at": now - timedelta(minutes=1),
            "record_count": 1500,
            "error_message": None,
            "extra": {},
        },
        {
            "run_id": "manual__entity",
            "airflow_dag_run_id": "manual__entity",
            "dag_id": "sap_successfactors_extract",
            "entity": "PerPhone",
            "status": "queued",
            "started_at": now - timedelta(minutes=40),
            "finished_at": None,
            "record_count": None,
            "error_message": None,
            "extra": {"airflow_observation": {"state": "queued", "observed_at": now.isoformat()}},
        },
    ]


@pytest.fixture()
def wired(monkeypatch):
    module = _module()
    loads: list[dict] = []
    refreshed: list[str] = []

    async def load(user, *, cartridge, run_ids):
        loads.append({"cartridge": cartridge, "run_ids": run_ids})
        return _rows()

    async def refresh(row, user=None):
        refreshed.append(row["run_id"])
        return row

    monkeypatch.setattr(module, "_load_progress_rows", load)
    monkeypatch.setattr(module.recovery_service, "default_refresh_dag_run_status", refresh)
    return loads, refreshed


def test_progress_maps_aggregate_and_entity_runs(wired):
    loads, refreshed = wired
    response = _client(READER).get(
        PATH,
        params=[("cartridge", "sap_successfactors"), ("run_id", "manual__all"), ("run_id", "manual__entity"), ("run_id", "manual__unknown")],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "pipeline-extraction-progress/v1"
    runs = {run["run_id"]: run for run in body["runs"]}
    assert set(runs) == {"manual__all", "manual__entity"}
    aggregate = runs["manual__all"]
    assert aggregate["phase"] == "extracting"
    assert aggregate["entities_done"] == 1
    assert aggregate["record_count"] == 1500
    entity = runs["manual__entity"]
    assert entity["phase"] == "connecting"
    assert entity["stalled"] is True
    assert entity["record_count"] is None
    assert loads == [{"cartridge": "sap_successfactors", "run_ids": ["manual__all", "manual__entity", "manual__unknown"]}]
    assert refreshed == ["manual__all"]


def test_progress_without_run_ids_reads_nothing(wired):
    loads, _refreshed = wired
    response = _client(READER).get(PATH, params={"cartridge": "sap_successfactors"})
    assert response.status_code == 200
    assert response.json()["runs"] == []
    assert loads == []


def test_progress_limits_and_validates_run_ids(wired):
    loads, _refreshed = wired
    client = _client(READER)
    too_many = [("cartridge", "sap_successfactors")] + [("run_id", f"r{i}") for i in range(21)]
    assert client.get(PATH, params=too_many).status_code == 422
    assert client.get(PATH, params=[("cartridge", "sap_successfactors"), ("run_id", "bad id")]).status_code == 422
    assert client.get(PATH, params={"cartridge": "../x", "run_id": "r1"}).status_code == 422
    assert loads == []


def test_progress_requires_pipelines_read(wired):
    loads, _refreshed = wired
    response = _client({**READER, "role": "workspace_user"}).get(PATH, params={"cartridge": "sap_successfactors", "run_id": "r1"})
    assert response.status_code == 403
    assert loads == []


def test_progress_refuses_foreign_cartridges_and_unscoped_users(wired):
    loads, _refreshed = wired
    assert _client(READER).get(PATH, params={"cartridge": "replicon", "run_id": "r1"}).status_code == 403
    unscoped = {key: value for key, value in READER.items() if key not in {"active_tenant_id", "active_workspace_id"}}
    assert _client(unscoped).get(PATH, params={"cartridge": "sap_successfactors", "run_id": "r1"}).status_code == 403
    assert loads == []


def test_progress_survives_an_airflow_refresh_failure(monkeypatch):
    module = _module()

    async def load(user, *, cartridge, run_ids):
        return _rows()[:1]

    async def broken_refresh(row, user=None):
        raise RuntimeError("airflow down")

    monkeypatch.setattr(module, "_load_progress_rows", load)
    monkeypatch.setattr(module.recovery_service, "default_refresh_dag_run_status", broken_refresh)
    response = _client(READER).get(PATH, params={"cartridge": "sap_successfactors", "run_id": "manual__all"})
    assert response.status_code == 200
    assert response.json()["runs"][0]["status"] == "running"


def test_progress_query_is_scoped_and_bounded(monkeypatch):
    module = _module()
    statements: list[tuple[str, tuple]] = []

    class Conn:
        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, sql, *args):
            statements.append((sql, args))

        async def fetch(self, sql, *args):
            statements.append((sql, args))
            return []

    class Pool:
        def acquire(self):
            return Conn()

    async def get_pool():
        return Pool()

    monkeypatch.setattr(module.recovery_service, "default_get_db_pool", get_pool)
    response = _client(READER).get(PATH, params={"cartridge": "sap_successfactors", "run_id": "manual__all"})
    assert response.status_code == 200
    assert "set_config('app.tenant_id'" in statements[0][0]
    assert statements[0][1] == (TENANT, WORKSPACE)
    select_sql, args = statements[1]
    assert "tenant_id = $1::uuid" in select_sql and "workspace_id = $2::uuid" in select_sql
    assert "LIMIT $5" in select_sql
    assert args[:4] == (TENANT, WORKSPACE, "sap_successfactors", ["manual__all"])
    assert args[4] == module.PROGRESS_ROW_LIMIT
    assert all(not sql.lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT")) for sql, _ in statements)

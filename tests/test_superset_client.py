from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest


REPO = Path(__file__).resolve().parents[1]
CSRF = "csrf-test-token"


@pytest.fixture()
def superset_client_module():
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import superset_client

    return superset_client


def _auth_transport(extra_handler=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "token-1"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": {"csrf_token": "csrf-1"}})
        if extra_handler:
            return extra_handler(request)
        return httpx.Response(404, json={"message": request.url.path})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_superset_login_returns_token(superset_client_module):
    client = superset_client_module.SupersetClient(
        base_url="http://superset:8088",
        username="admin",
        password="secret",
        transport=_auth_transport(),
    )

    result = await client.login()

    assert result["access_token"] == "token-1"
    assert result["csrf_token"] == "csrf-1"


@pytest.mark.asyncio
async def test_create_dataset_handles_409_existing(superset_client_module):
    def extra(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/dataset/" and request.method == "POST":
            return httpx.Response(409, json={"message": "already exists"})
        if request.url.path == "/api/v1/dataset/" and request.method == "GET":
            return httpx.Response(
                200,
                json={"result": [{"id": 42, "table_name": "gold_hours", "schema": "public", "database": {"id": 7}}]},
            )
        return httpx.Response(404)

    client = superset_client_module.SupersetClient(
        base_url="http://superset:8088",
        username="admin",
        password="secret",
        transport=_auth_transport(extra),
    )

    result = await client.create_dataset(7, "gold_hours", "public")

    assert result == {"dataset_id": 42, "table": "gold_hours", "schema": "public", "existing": True}


@pytest.mark.asyncio
async def test_create_dataset_retries_on_timeout(superset_client_module, monkeypatch):
    calls = {"post": 0}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(superset_client_module.asyncio, "sleep", no_sleep)

    def extra(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/dataset/" and request.method == "POST":
            calls["post"] += 1
            if calls["post"] < 3:
                raise httpx.ReadTimeout("transient", request=request)
            return httpx.Response(200, json={"id": 77})
        return httpx.Response(404)

    client = superset_client_module.SupersetClient(
        base_url="http://superset:8088",
        username="admin",
        password="secret",
        transport=_auth_transport(extra),
    )

    result = await client.create_dataset(7, "gold_hours", "public")

    assert calls["post"] == 3
    assert result["dataset_id"] == 77


@pytest.fixture()
def studio_client(monkeypatch):
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault("FIELD_ENCRYPTION_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.dependencies import require_authenticated
    from app.main import app
    from app.routers import studio as studio_router

    admin = {"id": 1, "email": "admin@local.ai", "role": "admin", "workspace_role": "admin"}
    app.dependency_overrides[require_authenticated] = lambda: admin
    app.dependency_overrides[studio_router.require_studio_write] = lambda: admin

    client = TestClient(app)
    client.cookies.set("csrf_token", CSRF)
    try:
        yield client, studio_router
    finally:
        app.dependency_overrides.pop(require_authenticated, None)
        app.dependency_overrides.pop(studio_router.require_studio_write, None)


def test_dataset_endpoint_returns_503_without_config(studio_client, monkeypatch):
    client, studio_router = studio_client

    class MissingClient:
        configured = False

    monkeypatch.setattr(studio_router.superset_client, "client_from_env", lambda: MissingClient())

    response = client.post(
        "/api/studio/superset/dataset",
        json={"database_id": 7, "table_name": "gold_hours"},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 503
    assert "Superset not configured" in response.text


def test_dataset_endpoint_audits_creation(studio_client, monkeypatch):
    client, studio_router = studio_client
    audits = []

    class FakeClient:
        configured = True

        async def list_databases(self):
            return [{"id": 7, "name": "modecissions_gold"}]

        async def create_dataset(self, database_id, table_name, schema="public"):
            return {"dataset_id": 9, "table": table_name, "schema": schema, "existing": False}

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    async def fake_datasets():
        return [{"name": "hours", "layer": "gold", "cartridge": "replicon"}]

    monkeypatch.setattr(studio_router.superset_client, "client_from_env", lambda: FakeClient())
    monkeypatch.setattr(studio_router.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_router, "_refinement_datasets", fake_datasets)

    response = client.post(
        "/api/studio/superset/dataset",
        json={"database_id": 7, "table_name": "gold_hours"},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert audits[0]["action"] == "studio.superset.dataset_create"

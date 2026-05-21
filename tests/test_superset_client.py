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


def test_superset_client_requires_service_account_in_production(superset_client_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SUPERSET_URL", "https://superset.internal")
    monkeypatch.delenv("SUPERSET_SERVICE_USER", raising=False)
    monkeypatch.delenv("SUPERSET_SERVICE_PASSWORD", raising=False)
    monkeypatch.setenv("SUPERSET_ADMIN_USER", "admin")
    monkeypatch.setenv("SUPERSET_ADMIN_PASSWORD", "admin-password")

    with pytest.raises(superset_client_module.SupersetConfigError, match="SERVICE_USER"):
        superset_client_module.SupersetClient()


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
async def test_superset_login_accepts_string_csrf_result(superset_client_module):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "token-1"})
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf-plain"})
        return httpx.Response(404)

    client = superset_client_module.SupersetClient(
        base_url="http://superset:8088",
        username="admin",
        password="secret",
        transport=httpx.MockTransport(handler),
    )

    result = await client.login()

    assert result["csrf_token"] == "csrf-plain"


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


def test_dataset_endpoint_returns_503_when_superset_unreachable(studio_client, monkeypatch):
    client, studio_router = studio_client

    class FailingClient:
        configured = True

        async def list_databases(self):
            raise studio_router.superset_client.SupersetRequestError(503, "Superset connection failed")

    async def fake_datasets(*_args, **_kwargs):
        return [{"name": "hours", "layer": "gold", "cartridge": "replicon"}]

    monkeypatch.setattr(studio_router.superset_client, "client_from_env", lambda: FailingClient())
    monkeypatch.setattr(studio_router, "_refinement_datasets", fake_datasets)

    response = client.post(
        "/api/studio/superset/dataset",
        json={"table_name": "gold_hours"},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Superset connection failed"


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

    async def fake_datasets(*_args, **_kwargs):
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


def test_dataset_endpoint_creates_gold_database_when_missing(studio_client, monkeypatch):
    client, studio_router = studio_client
    calls = []

    class FakeClient:
        configured = True

        async def list_databases(self):
            return []

        async def create_database(self, name, sqlalchemy_uri):
            calls.append(("database", name, sqlalchemy_uri))
            return {"id": 7, "name": name, "existing": False}

        async def create_dataset(self, database_id, table_name, schema="public"):
            calls.append(("dataset", database_id, table_name, schema))
            return {"dataset_id": 9, "table": table_name, "schema": schema, "existing": False}

    async def fake_audit(**kwargs):
        return None

    async def fake_datasets(*_args, **_kwargs):
        return [{"name": "hours", "layer": "gold", "cartridge": "replicon"}]

    monkeypatch.setenv("SUPERSET_GOLD_SQLALCHEMY_URI", "postgresql+psycopg2://gold@postgres_gold/modecissions_gold")
    monkeypatch.setattr(studio_router.superset_client, "client_from_env", lambda: FakeClient())
    monkeypatch.setattr(studio_router.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_router, "_refinement_datasets", fake_datasets)

    response = client.post(
        "/api/studio/superset/dataset",
        json={"table_name": "gold_hours"},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 200, response.text
    assert calls == [
        ("database", "modecissions_gold", "postgresql+psycopg2://gold@postgres_gold/modecissions_gold"),
        ("dataset", 7, "gold_hours", "public"),
    ]


def test_dataset_endpoint_returns_materialization_hint_for_missing_gold_table(studio_client, monkeypatch):
    client, studio_router = studio_client

    class FakeClient:
        configured = True

        async def list_databases(self):
            return [{"id": 7, "name": "modecissions_gold"}]

        async def create_dataset(self, database_id, table_name, schema="public"):
            raise studio_router.superset_client.SupersetRequestError(
                422,
                "Superset create dataset failed with HTTP 422: Table [gold_hours] could not be found",
            )

    async def fake_datasets(*_args, **_kwargs):
        return [{"name": "hours", "layer": "gold", "cartridge": "replicon"}]

    monkeypatch.setattr(studio_router.superset_client, "client_from_env", lambda: FakeClient())
    monkeypatch.setattr(studio_router, "_refinement_datasets", fake_datasets)

    response = client.post(
        "/api/studio/superset/dataset",
        json={"table_name": "gold_hours"},
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["needs_materialization"] is True
    assert body["table"] == "gold_hours"


def test_dag_delete_passes_cartridge_scope(studio_client, monkeypatch):
    client, studio_router = studio_client
    invoked = []

    async def fake_get_cartridge(cartridge):
        return {"id": cartridge, "name": cartridge, "dags": [{"dag_id": "replicon_extract"}], "entities": []}

    async def fake_invoke(server, tool, payload, **_kwargs):
        invoked.append((server, tool, payload))
        return {"deleted_file": True, "deleted_db": True}

    async def fake_audit(**kwargs):
        return None

    monkeypatch.setattr(studio_router.cartridge_service, "get_cartridge", fake_get_cartridge)
    monkeypatch.setattr(studio_router.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_router.audit_service, "record_event", fake_audit)

    response = client.delete(
        "/api/studio/dags/replicon_extract?cartridge=replicon",
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 200, response.text
    assert invoked == [
        ("infra", "airflow_delete_dag", {"dag_id": "replicon_extract", "cartridge_id": "replicon"})
    ]


def test_dag_delete_rejects_other_cartridge_prefix(studio_client, monkeypatch):
    client, studio_router = studio_client
    invoked = []

    async def fake_get_cartridge(cartridge):
        return {"id": cartridge, "name": cartridge, "dags": [{"dag_id": "replicon_extract"}], "entities": []}

    async def fake_invoke(server, tool, payload, **_kwargs):
        invoked.append((server, tool, payload))
        return {"deleted_file": True, "deleted_db": True}

    monkeypatch.setattr(studio_router.cartridge_service, "get_cartridge", fake_get_cartridge)
    monkeypatch.setattr(studio_router.mcp_registry, "invoke", fake_invoke)

    response = client.delete(
        "/api/studio/dags/platform_scheduler?cartridge=replicon",
        headers={"X-CSRF-Token": CSRF},
    )

    assert response.status_code == 403
    assert invoked == []

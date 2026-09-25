from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

VIEWER = {"id": 1, "role": "viewer"}
WORKSPACE_ADMIN = {"id": 3, "role": "user", "workspace_role": "workspace_admin"}
TENANT_ADMIN = {"id": 4, "role": "user", "workspace_role": "tenant_admin"}
SCOPE = {"tenant_id": "11111111-1111-1111-1111-111111111111", "workspace_id": "22222222-2222-2222-2222-222222222222"}


def _app(user: dict, *routers, overrides: dict | None = None) -> TestClient:
    app = FastAPI()
    app.dependency_overrides.update(overrides or {})

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = dict(user)
        return await call_next(request)

    for router in routers:
        app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path", ("/control-room/sap-b1", "/control-room/sap-b1/"))
def test_sap_b1_page_uses_the_talent_permissions(monkeypatch, path):
    from app.routers import pages

    monkeypatch.setattr(pages, "_console_next_response", lambda request, path="index.html": PlainTextResponse(path))

    assert _app(VIEWER, pages.router).get(path).status_code == 403
    assert _app(WORKSPACE_ADMIN, pages.router).get(path).status_code == 403
    response = _app(TENANT_ADMIN, pages.router).get(path)
    assert response.status_code == 200
    assert response.text == "control-room/sap-b1/index.html"


def _views_client(monkeypatch, user: dict) -> tuple[TestClient, list]:
    from app.routers import control_room
    from app.services import control_room_service

    calls: list = []

    async def passthrough(namespace, _user, loader):
        calls.append(namespace)
        return await loader()

    async def margin(_user, *, top_n=0):
        calls.append(("margin", top_n))
        return {"domain": "sap_b1_margin", "status": "ready", "metrics": {"modelo_entidades": {
            "status": "ready", "entities": [{"entity": "cliente", "company": "grupo", "records": 3, "orphans": 0}]}}}

    async def semaforo(_user):
        calls.append(("semaforo",))
        return {"domain": "sap_b1_semaforo", "status": "degraded", "metrics": {"caducidad_lotes": {
            "status": "ready", "breaches": ["Hay 2 lotes ya vencidos."]}}}

    monkeypatch.setattr(control_room, "_control_room_cache_get_or_set", passthrough)
    monkeypatch.setattr(control_room_service, "sap_b1_margin_kpis", margin)
    monkeypatch.setattr(control_room_service, "sap_b1_semaforo_kpis", semaforo)
    async def current_user():
        return dict(user)

    return _app(user, control_room.router, overrides={control_room.require_authenticated: current_user}), calls


def test_sap_b1_views_route_serves_the_projected_view(monkeypatch):
    client, calls = _views_client(monkeypatch, {**VIEWER, **SCOPE, "allowed_cartridges": ["sap_b1"]})

    response = client.get("/api/control-room/sap-b1/views/sap_b1_margin_kpis?top_n=3")
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["modelo_entidades"]["entities"][0]["entity"] == "cliente"
    assert calls[:2] == ["sap-b1-margin-kpis-3", ("margin", 3)]

    semaforo = client.get("/api/control-room/sap-b1/views/sap_b1_semaforo_kpis")
    assert semaforo.status_code == 200
    assert semaforo.json()["metrics"]["caducidad_lotes"]["breaches"] == ["Hay 2 lotes ya vencidos."]
    assert "sap-b1-semaforo-kpis" in calls


def test_sap_b1_views_route_rejects_other_views_scopes_and_permissions(monkeypatch):
    client, calls = _views_client(monkeypatch, {**VIEWER, **SCOPE, "allowed_cartridges": ["sap_b1"]})
    assert client.get("/api/control-room/sap-b1/views/finance_kpis").status_code == 404
    assert client.get("/api/control-room/sap-b1/views/sap_b1_margin_kpis?top_n=50").status_code == 422

    other, _ = _views_client(monkeypatch, {**VIEWER, **SCOPE, "allowed_cartridges": ["sap_successfactors"]})
    assert other.get("/api/control-room/sap-b1/views/sap_b1_margin_kpis").status_code == 403

    no_read, _ = _views_client(monkeypatch, {"id": 2, "role": "workspace_user", **SCOPE, "allowed_cartridges": ["sap_b1"]})
    assert no_read.get("/api/control-room/sap-b1/views/sap_b1_margin_kpis").status_code == 403
    assert calls == []


def test_internal_read_still_dispatches_sap_b1_views(monkeypatch):
    from app.routers import control_room

    _client, calls = _views_client(monkeypatch, VIEWER)
    user = {**SCOPE, "_effective_permissions": ["datasets.read"]}
    result = asyncio.run(control_room._control_room_internal_view("sap_b1_semaforo_kpis", user, {}))
    assert result.model_dump()["domain"] == "sap_b1_semaforo"
    assert calls == ["sap-b1-semaforo-kpis", ("semaforo",)]


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_overview_reads_the_connector_heartbeat_with_a_signed_header(monkeypatch):
    from app.routers import sap_b1

    requests: list[tuple[str, str, dict]] = []
    heartbeat = {"present": True, "readable": True, "age_seconds": 120, "source_ok": True,
                 "companies": ["mx"], "initial_load": {"state": "done", "months_done": 24, "months_total": 24}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            requests.append(("GET", url, dict(headers or {})))
            return _Response(200, heartbeat)

    class FakeConn:
        async def fetchval(self, *args):
            return "active"

        async def fetchrow(self, *args):
            return None

    @asynccontextmanager
    async def scoped(_pool, _user):
        yield FakeConn(), SCOPE["tenant_id"], SCOPE["workspace_id"]

    async def no_pool():
        return object()

    async def recipients(*_args):
        return ["direccion@example.com"]

    async def connection(_user):
        return None

    async def catalog():
        return [{"key": "margin_min_pct", "default": None}]

    async def dags(_user):
        return [{"dag_id": "sap_b1_refresh", "present": True, "paused": True}]

    monkeypatch.setattr(sap_b1.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(sap_b1.auth, "pool", no_pool)
    monkeypatch.setattr(sap_b1, "scoped_db_for_user", scoped)
    monkeypatch.setattr(sap_b1.sap_b1_digest, "recipients", recipients)
    monkeypatch.setattr(sap_b1, "_connection", connection)
    monkeypatch.setattr(sap_b1, "_catalog", catalog)
    monkeypatch.setattr(sap_b1, "_dag_states", dags)
    monkeypatch.setattr(sap_b1, "_signed_context", lambda user: {"trusted": True, **SCOPE})
    monkeypatch.setattr(sap_b1, "_internal_headers", lambda service: {"X-Internal-Key": "k"})

    response = _app({**TENANT_ADMIN, **SCOPE}, sap_b1.router).get("/api/sap-b1/overview")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["installed"] == "active"
    assert body["connector"] == heartbeat
    assert body["parameters"] == {"loaded": False, "keys_total": 1, "keys_set": 0, "missing": ["margin_min_pct"],
                                  "using_default": [], "branches": 0, "accounts": 0, "valid": True}
    assert body["digest"]["recipients"] == 1 and body["digest"]["last"] is None
    [(method, url, headers)] = requests
    assert method == "GET" and url.endswith("/connector/status")
    assert headers["X-Internal-Key"] == "k"
    assert json.loads(headers["x-security-context"]) == {"trusted": True, **SCOPE}


def test_sap_b1_routes_only_use_registered_permissions():
    from app.routers import sap_b1
    from app.services import permissions

    required = {
        (route.path, method): {getattr(dep.call, "required_permission", None) for dep in route.dependant.dependencies}
        - {None}
        for route in sap_b1.router.routes
        for method in route.methods
    }
    assert all(perms and perms <= permissions.PERMISSION_KEYS for perms in required.values()), required
    for path in ("/api/sap-b1/overview", "/api/sap-b1/indicators", "/api/sap-b1/mapping"):
        assert required[(path, "GET")] == {"datasets.read"}
    for path in ("/api/sap-b1/business-parameters", "/api/sap-b1/recipients"):
        assert required[(path, "GET")] == {"control_room.write"}

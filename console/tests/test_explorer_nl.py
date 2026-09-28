from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.data_platform.scoped_reads import scoped_read_cache_invalidate
from app.routers import data_explorer
from app.services import llm_client


MAKER = {
    "id": 7,
    "email": "maker@example.test",
    "role": "workspace_admin",
    "active_tenant_id": "tenant-aaa",
    "active_workspace_id": "workspace-bbb",
    "allowed_cartridges": ["acme"],
}
VIEWER = {**MAKER, "id": 8, "role": "viewer"}
FIELDS = [
    {"name": "nombre", "type": "VARCHAR"},
    {"name": "salario", "type": "DOUBLE"},
    {"name": "load_date", "type": "DATE"},
]
GOLD = "gold/ventas"
BRONZE = "raw/acme/Employee"
VALID_SPEC = {
    "columns": ["nombre", "salario"],
    "filters": [{"column": "salario", "op": "gt", "value": 20000}],
    "sort": [{"column": "salario", "direction": "desc"}],
    "limit": 10,
    "latest_only": False,
}


class FakeRefinement:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, tool, args, *, timeout, user, **_deps):
        self.calls.append((tool, args))
        if tool == "describe_source":
            return {"source": args["source"], "fields": FIELDS, "sample": [], "error": None}
        if tool == "preview_transform" and "WHERE 1 = 0" in args["sql"]:
            return {"schema": FIELDS, "data": [], "row_count": 0}
        return {
            "schema": FIELDS,
            "data": [{"nombre": "Ana", "salario": 30000.0, "load_date": "2026-09-01"}],
            "row_count": 1,
        }


def _client(user, *, csrf=True):
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request, call_next):
        request.state.user = user
        return await call_next(request)

    app.include_router(data_explorer.router)
    client = TestClient(app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "session")
    if csrf:
        client.cookies.set("csrf_token", "csrf-ok")
        client.headers["X-CSRF-Token"] = "csrf-ok"
    return client


@pytest.fixture(autouse=True)
def _fresh_schema_cache():
    scoped_read_cache_invalidate(data_explorer.SCHEMA_CACHE_NAMESPACE)
    yield
    scoped_read_cache_invalidate(data_explorer.SCHEMA_CACHE_NAMESPACE)


@pytest.fixture()
def refinement(monkeypatch):
    fake = FakeRefinement()
    monkeypatch.setattr(data_explorer, "refinement_invoke", fake)
    return fake


@pytest.fixture()
def llm(monkeypatch):
    state = {"reply": json.dumps(VALID_SPEC), "calls": []}

    async def fake_chat(**kw):
        state["calls"].append(kw)
        exc = state.get("raise")
        if exc is not None:
            raise exc
        return (state["reply"], [], [])

    monkeypatch.setattr(data_explorer.llm_client, "chat", fake_chat)
    return state


def _nl(client, **body):
    return client.post("/api/data/explore/nl", json=body)


def test_nl_route_declares_csrf_and_dataset_read_permission():
    route = next(
        route for route in data_explorer.router.routes
        if route.path == "/api/data/explore/nl"
    )
    assert route.methods == {"POST"}
    names = {getattr(dep.dependency, "__name__", "") for dep in route.dependencies}
    assert "require_csrf" in names
    assert "require_permission_datasets_read" in names


def test_nl_route_is_mounted_on_the_console_app():
    from app.main import app

    matches = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/api/data/explore/nl"
        and "POST" in getattr(route, "methods", set())
    ]
    assert len(matches) == 1


def test_valid_llm_spec_is_compiled_but_not_executed(refinement, llm):
    response = _nl(_client(MAKER), source=GOLD, question="ventas con salario mayor a 20000")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"] == "ventas con salario mayor a 20000"
    assert body["spec"]["filters"] == [
        {"column": "salario", "op": "gt", "value": 20000, "values": None}
    ]
    assert body["spec"]["limit"] == 10
    assert body["executed"] is False
    assert body["rows"] == []
    assert body["row_count"] == 0
    assert '"salario" > 20000' in body["sql_display"]
    executed = [
        args
        for tool, args in refinement.calls
        if tool == "preview_transform" and "WHERE 1 = 0" not in args["sql"]
    ]
    assert executed == []
    system = llm["calls"][0]["system"]
    assert "nombre:text" in system
    assert "salario:number" in system


def test_invalid_json_output_is_rejected(refinement, llm):
    llm["reply"] = "claro, aquí tienes los filtros que pediste"
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 422
    assert "plan de consulta" in response.json()["detail"]
    assert all(
        "WHERE 1 = 0" in args["sql"]
        for tool, args in refinement.calls
        if tool == "preview_transform"
    )


def test_sql_looking_output_is_rejected(refinement, llm):
    llm["reply"] = "SELECT * FROM read_parquet('raw/acme/Employee') LIMIT 10"
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 422
    assert "SQL" in response.json()["detail"]


def test_spec_with_unknown_keys_is_rejected(refinement, llm):
    llm["reply"] = json.dumps({**VALID_SPEC, "sql": "SELECT 1"})
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 422
    assert "claves no permitidas" in response.json()["detail"]


def test_spec_failing_deterministic_validation_is_rejected(refinement, llm):
    llm["reply"] = json.dumps(
        {"filters": [{"column": "inexistente", "op": "regex", "value": "x"}]}
    )
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 422
    assert "no pasó la validación" in response.json()["detail"]


def test_no_llm_provider_returns_503(refinement, llm):
    llm["raise"] = llm_client.LLMConfigurationError("no key")
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 503
    assert response.json()["detail"] == "Sin conexión al asistente"


def test_provider_error_returns_503(refinement, llm):
    llm["raise"] = llm_client.LLMProviderError("upstream down")
    response = _nl(_client(MAKER), source=GOLD, question="dame todo")
    assert response.status_code == 503
    assert response.json()["detail"] == "Sin conexión al asistente"


def test_authorization_parity_with_explore(refinement, llm):
    assert _nl(_client(None), source=GOLD, question="q").status_code == 401
    assert _nl(_client(MAKER, csrf=False), source=GOLD, question="q").status_code == 403
    assert _nl(
        _client({**MAKER, "role": "workspace_user"}), source=GOLD, question="q"
    ).status_code == 403

    bronze_denied = _nl(_client(VIEWER), source=BRONZE, question="q")
    assert bronze_denied.status_code == 403
    assert bronze_denied.json()["detail"] == "permission required: datasets.write"

    wrong_cartridge = _nl(
        _client({**MAKER, "allowed_cartridges": ["beta"]}), source=BRONZE, question="q"
    )
    assert wrong_cartridge.status_code == 403

    assert llm["calls"] == []
    assert refinement.calls == []


def test_invalid_source_string_is_rejected_before_llm(refinement, llm):
    response = _nl(_client(MAKER), source="silver/otra/cosa", question="q")
    assert response.status_code == 400
    assert llm["calls"] == []


def test_question_bounds_are_enforced(refinement, llm):
    assert _nl(_client(MAKER), source=GOLD, question="").status_code == 422
    assert _nl(_client(MAKER), source=GOLD, question="x" * 501).status_code == 422
    assert _nl(
        _client(MAKER), source=GOLD, question="q", extra="nope"
    ).status_code == 422
    assert llm["calls"] == []


def test_bronze_source_compiles_with_write_permission(refinement, llm):
    llm["reply"] = json.dumps({"filters": [], "limit": 5, "latest_only": True})
    response = _nl(_client(MAKER), source=BRONZE, question="lo más reciente")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "raw/acme/Employee"
    assert body["spec"]["latest_only"] is True
    assert body["sources"] == ["raw/acme/Employee"]
    assert body["executed"] is False
    assert all(tool != "preview_transform" or "WHERE 1 = 0" in args["sql"] for tool, args in refinement.calls)


def test_in_values_with_commas_round_trip_in_the_spec(refinement, llm):
    llm["reply"] = json.dumps(
        {"filters": [{"column": "nombre", "op": "in", "values": ["García, Juan", "Ana"]}]}
    )
    response = _nl(_client(MAKER), source=GOLD, question="de García, Juan o Ana")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spec"]["filters"] == [
        {"column": "nombre", "op": "in", "value": None, "values": ["García, Juan", "Ana"]}
    ]

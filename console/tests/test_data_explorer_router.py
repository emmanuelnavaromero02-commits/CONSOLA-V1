from __future__ import annotations

import json
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.data_platform.scoped_reads import scoped_read_cache_invalidate
from app.routers import data_explorer


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
    {"name": "meta", "type": "STRUCT(a INTEGER)"},
]
BRONZE = {"kind": "bronze", "cartridge": "acme", "entity": "Employee"}
DATASET = {"kind": "dataset", "name": "ventas"}


class FakeRefinement:
    def __init__(self, *, fields=None, rows=None, fail=None):
        self.calls: list[tuple[str, dict]] = []
        self.fields = FIELDS if fields is None else fields
        self.rows = rows if rows is not None else [
            {"nombre": "Ana", "salario": 10.5, "load_date": "2026-09-01", "meta": {"a": 1}}
        ]
        self.fail = fail

    async def __call__(self, tool, args, *, timeout, user, **_deps):
        self.calls.append((tool, args))
        if self.fail is not None:
            raise self.fail
        if tool == "describe_source":
            return {"source": args["source"], "fields": self.fields, "sample": [], "error": None}
        if tool == "preview_transform" and "WHERE 1 = 0" in args["sql"]:
            return {"schema": self.fields, "data": [], "row_count": 0}
        return {"schema": self.fields, "data": self.rows, "row_count": len(self.rows)}


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


def _explore(client, **body):
    return client.post("/api/data/explore", json=body)


def test_route_declares_csrf_and_dataset_read_permission():
    route = next(route for route in data_explorer.router.routes if route.path == "/api/data/explore")
    assert route.methods == {"POST"}
    names = {getattr(dep.dependency, "__name__", "") for dep in route.dependencies}
    assert "require_csrf" in names
    assert "require_permission_datasets_read" in names


def test_route_is_mounted_on_the_console_app():
    from app.main import app

    matches = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/api/data/explore" and "POST" in getattr(route, "methods", set())
    ]
    assert len(matches) == 1


def test_missing_csrf_token_is_rejected(refinement):
    response = _explore(_client(MAKER, csrf=False), source=DATASET)
    assert response.status_code == 403
    assert "csrf" in response.json()["detail"]
    assert refinement.calls == []


def test_anonymous_requests_are_rejected(refinement):
    response = _explore(_client(None), source=DATASET)
    assert response.status_code == 401
    assert refinement.calls == []


def test_users_without_dataset_read_are_rejected(refinement):
    response = _explore(_client({**MAKER, "role": "workspace_user"}), source=DATASET)
    assert response.status_code == 403
    assert refinement.calls == []


def test_bronze_requires_dataset_write_like_bronze_query(refinement):
    response = _explore(_client(VIEWER), source=BRONZE)
    assert response.status_code == 403
    assert response.json()["detail"] == "permission required: datasets.write"
    assert refinement.calls == []


def test_bronze_requires_the_cartridge_in_the_active_workspace(refinement):
    response = _explore(_client({**MAKER, "allowed_cartridges": ["beta"]}), source=BRONZE)
    assert response.status_code == 403
    assert refinement.calls == []


def test_bronze_requires_tenant_and_workspace_scope(refinement):
    unscoped = {"id": 1, "email": "admin@example.test", "role": "admin"}
    response = _explore(_client(unscoped), source=BRONZE)
    assert response.status_code == 400
    assert refinement.calls == []


def test_viewer_can_explore_gold_datasets_with_dataset_cap(refinement):
    response = _explore(
        _client(VIEWER),
        source=DATASET,
        columns=["nombre", "salario"],
        filters=[{"column": "salario", "op": "gt", "value": "5"}],
        limit=50_000,
    )
    assert response.status_code == 200, response.text
    schema_call, query_call = refinement.calls
    assert schema_call[0] == "preview_transform"
    assert schema_call[1]["sql"] == "SELECT * FROM pggold.gold_ventas WHERE 1 = 0"
    assert query_call[1]["sql"].startswith('SELECT "nombre", "salario" FROM pggold.gold_ventas WHERE')
    assert query_call[1]["params"] == ["5"]
    assert query_call[1]["limit"] == 10_000
    assert "sources" not in query_call[1]
    assert query_call[1]["user_context"]["workspace_id"] == "workspace-bbb"
    payload = response.json()
    assert payload["limit"] == 10_000
    assert payload["columns"] == ["nombre", "salario"]
    assert payload["rows"] == [["Ana", 10.5]]
    assert payload["source"] == "gold/ventas"


def test_bronze_execute_rewrites_to_scope_but_displays_logical_paths(refinement):
    response = _explore(
        _client(MAKER),
        source=BRONZE,
        filters=[{"column": "nombre", "op": "contains", "value": "x' OR 1=1 --"}],
        sort=[{"column": "salario", "direction": "desc"}],
        latest_only=True,
        limit=9_999,
    )
    assert response.status_code == 200, response.text
    (describe, describe_args), (tool, args) = refinement.calls
    assert describe == "describe_source"
    assert describe_args == {"source": "raw/acme/Employee", "schema_only": True}
    assert tool == "preview_transform"
    assert "tenant_id=tenant-aaa/workspace_id=workspace-bbb" in args["sql"]
    assert "x' OR 1=1" not in args["sql"]
    assert args["params"] == ["x' OR 1=1 --"]
    assert args["limit"] == 2000
    assert args["sources"] == ["raw/acme/Employee"]
    assert "'{latest_date}'" in args["sql"]
    payload = response.json()
    assert payload["sql_display"].startswith("SELECT * FROM read_parquet('raw/acme/Employee') WHERE")
    assert "x'' OR 1=1 --" in payload["sql_display"]
    for secret in ("s3://", "tenant-aaa", "workspace-bbb", "tenant_id="):
        assert secret not in payload["sql_display"]
    assert payload["columns"] == ["nombre", "salario", "load_date", "meta"]
    assert payload["rows"] == [["Ana", 10.5, "2026-09-01", '{"a": 1}']]
    assert payload["available_columns"][3] == {"name": "meta", "type": "STRUCT(a INTEGER)", "kind": "other"}
    assert payload["executed"] is True
    assert payload["truncated"] is False
    assert payload["sql_definition"].startswith(
        "SELECT * FROM read_parquet('s3://{bucket}/raw/acme/Employee/**/*.parquet'"
    )
    assert "tenant" not in payload["sql_definition"]


def test_execute_mode_omits_definition_when_values_cannot_be_saved(refinement):
    response = _explore(
        _client(MAKER),
        source=BRONZE,
        filters=[{"column": "nombre", "op": "eq", "value": "{latest_date}"}],
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sql_definition"] is None
    assert refinement.calls[-1][1]["params"] == ["{latest_date}"]


def test_definition_mode_compiles_server_side_without_reading_rows(refinement):
    response = _explore(
        _client(MAKER),
        source=BRONZE,
        execute=False,
        columns=["nombre"],
        filters=[{"column": "nombre", "op": "eq", "value": "O'Reilly"}],
    )
    assert response.status_code == 200, response.text
    assert [tool for tool, _ in refinement.calls] == ["describe_source"]
    payload = response.json()
    assert payload["executed"] is False
    assert payload["rows"] == []
    assert payload["sources"] == ["raw/acme/Employee"]
    assert payload["sql_definition"] == (
        "SELECT \"nombre\" FROM read_parquet('s3://{bucket}/raw/acme/Employee/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true) WHERE CAST(\"nombre\" AS VARCHAR) = 'O''Reilly'"
    )
    assert payload["sql_display"] == payload["sql_definition"]


@pytest.mark.parametrize(
    ("body", "status", "fragment"),
    [
        ({"source": {"kind": "sql", "sql": "select 1"}}, 400, "Tipo de fuente"),
        ({"source": {**BRONZE, "entity": "E'); DROP TABLE x; --"}}, 400, "entidad"),
        ({"source": DATASET, "columns": ["nombre; DROP TABLE x"]}, 400, "no existe"),
        ({"source": DATASET, "filters": [{"column": "nombre", "op": "gt", "value": "a"}]}, 400, "no aplica"),
        ({"source": DATASET, "latest_only": True}, 400, "bronze"),
        ({"source": DATASET, "execute": "yes"}, 400, "execute"),
        ({"source": BRONZE, "execute": False, "filters": [{"column": "nombre", "op": "eq", "value": "{bucket}"}]}, 400, "llaves"),
    ],
)
def test_invalid_specs_fail_closed_with_spanish_detail(refinement, body, status, fragment):
    response = _explore(_client(MAKER), **body)
    assert response.status_code == status
    assert fragment in response.json()["detail"]
    assert all(tool != "preview_transform" or "WHERE 1 = 0" in args["sql"] for tool, args in refinement.calls)


def test_empty_schema_is_reported(monkeypatch):
    monkeypatch.setattr(data_explorer, "refinement_invoke", FakeRefinement(fields=[]))
    response = _explore(_client(MAKER), source=BRONZE)
    assert response.status_code == 404
    assert "columnas" in response.json()["detail"]


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _fake_httpx(responses, captured):
    class AsyncClient:
        def __init__(self, *, headers, timeout):
            captured.append({"headers": headers, "timeout": timeout})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):
            captured.append({"url": url, "json": json})
            return responses.pop(0)

    class TimeoutException(Exception):
        pass

    class TransportError(Exception):
        pass

    return types.SimpleNamespace(
        AsyncClient=AsyncClient, TimeoutException=TimeoutException, TransportError=TransportError
    )


def test_upstream_errors_never_echo_scoped_storage_uris(monkeypatch):
    captured: list[dict] = []
    scoped = "s3://lakehouse/raw/acme/Employee/tenant_id=tenant-aaa/workspace_id=workspace-bbb/**/*.parquet"
    responses = [
        _FakeResponse(200, {"source": "raw/acme/Employee", "fields": FIELDS, "sample": [], "error": None}),
        _FakeResponse(200, {"error": f"Binder Error: bad cast reading {scoped}", "raw_error": scoped}),
    ]
    monkeypatch.setattr(data_explorer, "httpx", _fake_httpx(responses, captured))
    response = _explore(_client(MAKER), source=BRONZE)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("No se pudo ejecutar la consulta")
    for secret in ("s3://", "tenant-aaa", "workspace-bbb"):
        assert secret not in detail
    posted = [item["json"] for item in captured if "json" in item]
    assert posted[0]["tool"] == "describe_source"
    assert posted[0]["args"] == {"source": "raw/acme/Employee", "schema_only": True}
    assert posted[0]["security_context"]["workspace_id"] == "workspace-bbb"
    assert posted[1]["tool"] == "preview_transform"
    headers = [item["headers"] for item in captured if "headers" in item]
    assert all(header["x-internal-service"] == "console" for header in headers)


def test_missing_parquet_is_explained_in_spanish(monkeypatch):
    captured: list[dict] = []
    responses = [
        _FakeResponse(
            200,
            {
                "source": "raw/acme/Employee",
                "fields": [],
                "sample": [],
                "error": "IO Error: No files found that match the pattern s3://lakehouse/raw/acme/Employee/tenant_id=tenant-aaa/x",
            },
        )
    ]
    monkeypatch.setattr(data_explorer, "httpx", _fake_httpx(responses, captured))
    response = _explore(_client(MAKER), source=BRONZE)
    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Esta fuente de datos todavía no tiene archivos cargados. Ejecuta primero la extracción."
    )


@pytest.mark.parametrize(
    ("status", "detail", "expected_status", "expected"),
    [
        (200, {"error": "Query exceeded timeout of 30s. Try a more restrictive WHERE clause"}, 422, "tiempo máximo"),
        (200, {"error": "INTERRUPT Error: Interrupted!"}, 422, "tiempo máximo"),
        (404, {"detail": "Binder Error: Referenced column does not exist"}, 422, "No se pudo ejecutar la consulta: Binder Error"),
        (403, {"detail": "SQL table function or storage path is not allowed"}, 403, "rechazó la consulta"),
        (500, {"detail": "boom"}, 500, "No se pudo ejecutar la consulta"),
    ],
)
def test_engine_failures_map_to_readable_statuses(monkeypatch, status, detail, expected_status, expected):
    captured: list[dict] = []
    responses = [
        _FakeResponse(200, {"source": "raw/acme/Employee", "fields": FIELDS, "sample": [], "error": None}),
        _FakeResponse(status, detail),
    ]
    monkeypatch.setattr(data_explorer, "httpx", _fake_httpx(responses, captured))
    response = _explore(_client(MAKER), source=BRONZE)
    assert response.status_code == expected_status
    assert expected in response.json()["detail"]
    assert len(response.json()["detail"]) <= 240


def test_transport_failures_are_reported_as_unavailable(monkeypatch):
    fake = _fake_httpx([], [])

    class Failing(fake.AsyncClient):
        async def post(self, url, json):
            raise fake.TransportError("connection refused")

    fake.AsyncClient = Failing
    monkeypatch.setattr(data_explorer, "httpx", fake)
    response = _explore(_client(MAKER), source=BRONZE)
    assert response.status_code == 503
    assert response.json()["detail"].startswith("El motor de datos no está disponible")


def test_response_model_forbids_extra_fields():
    assert data_explorer.ExplorerResponse.model_config.get("extra") == "forbid"
    assert data_explorer.ExplorerColumnOut.model_config.get("extra") == "forbid"
    annotations = {
        name: str(field.annotation)
        for name, field in data_explorer.ExplorerResponse.model_fields.items()
    }
    assert not any("dict" in value or "Any" in value for value in annotations.values())


def test_schema_is_cached_per_scoped_identity_and_source(refinement, monkeypatch):
    client = _client(MAKER)
    assert _explore(client, source=BRONZE, execute=False).status_code == 200
    assert _explore(client, source=BRONZE, execute=False).status_code == 200
    assert [tool for tool, _ in refinement.calls] == ["describe_source"]
    other_workspace = {**MAKER, "active_workspace_id": "workspace-ccc"}
    assert _explore(_client(other_workspace), source=BRONZE, execute=False).status_code == 200
    assert [tool for tool, _ in refinement.calls] == ["describe_source", "describe_source"]
    other_source = {**BRONZE, "entity": "Invoice"}
    assert _explore(client, source=other_source, execute=False).status_code == 200
    assert len(refinement.calls) == 3


def test_schema_cache_never_bypasses_authorization(refinement):
    assert _explore(_client(MAKER), source=BRONZE, execute=False).status_code == 200
    denied = _explore(_client({**MAKER, "allowed_cartridges": ["beta"]}), source=BRONZE, execute=False)
    assert denied.status_code == 403
    viewer = _explore(_client(VIEWER), source=BRONZE, execute=False)
    assert viewer.status_code == 403
    assert len(refinement.calls) == 1


def test_sap_b1_routes_have_a_surface_classification():
    from app.route_surface_registry import classify_route_surface

    assert classify_route_surface("/api/sap-b1/overview") == "frontend"
    assert classify_route_surface("/api/data/explore") == "frontend"

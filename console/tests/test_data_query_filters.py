from __future__ import annotations

import importlib
import json
import sys
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


INTERNAL_KEY = "test_internal_api_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


async def _noop_async(*args, **kwargs):
    return None


async def _empty_list_async(*args, **kwargs):
    return []


@pytest.fixture()
def console_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", "test_jwt_secret_key_with_more_than_32_chars")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

    async def _fake_get_session_user(token):
        if token == "fake-session-token":
            return {
                "user_id": 99, "id": 99, "email": "test@test.com", "role": "admin",
                "workspace_role": "admin",
                "active_tenant_id": "t-active", "tenant_id": "t-fallback",
                "active_workspace_id": "ws-active", "workspace_id": "ws-fallback",
                "project_id": "proj-1",
            }
        return None

    auth_stub = _module(
        COOKIE_NAME="mod_session",
        REFRESH_COOKIE_NAME="refresh_token",
        close_pool=_noop_async,
        cookie_secure=lambda: False,
        verify_internal_api_key=lambda *args, **kwargs: None,
        get_session_user=_fake_get_session_user,
    )

    asyncpg_stub = _module(executed=[], fetch_rows=[])

    class FakePool:
        async def execute(self, query, *args):
            asyncpg_stub.executed.append((query, args))
            return "OK"

        async def fetch(self, *args, **kwargs):
            return list(asyncpg_stub.fetch_rows)

        async def fetchrow(self, *args, **kwargs):
            return asyncpg_stub.fetch_rows[0] if asyncpg_stub.fetch_rows else None

        async def close(self):
            return None

    async def create_pool(*args, **kwargs):
        return FakePool()

    asyncpg_stub.create_pool = create_pool

    service_stubs = {
        "app.services.auth": auth_stub,
        "app.services.tokens": _module(close_pool=_noop_async),
        "app.services.email_service": _module(),
        "app.services.mcp_registry": _module(
            invoke=_noop_async,
            startup=_noop_async,
            health_check_all=_noop_async,
            close_pool=_noop_async,
        ),
        "app.services.assistant": _module(),
        "app.services.studio_assistant": _module(register_local_tool=lambda *args, **kwargs: None),
        "app.services.token_store": _module(close_pool=_noop_async),
        "app.services.job_service": _module(list_recent=_empty_list_async, close_pool=_noop_async),
        "app.services.cartridge_service": _module(get_cartridge=_noop_async, close_pool=_noop_async),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)

    import app.services as _svc_pkg
    for attr, mod in [
        ("auth", service_stubs["app.services.auth"]),
        ("job_service", service_stubs["app.services.job_service"]),
        ("token_store", service_stubs["app.services.token_store"]),
        ("mcp_registry", service_stubs["app.services.mcp_registry"]),
        ("cartridge_service", service_stubs["app.services.cartridge_service"]),
    ]:
        monkeypatch.setattr(_svc_pkg, attr, mod, raising=False)

    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.routers.studio", None)
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)


def _make_client(main, *, authenticated: bool = True) -> TestClient:
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=True)

    if authenticated:
        client.cookies.set("mod_session", "fake-session-token")

        from app.dependencies import require_authenticated
        main.app.dependency_overrides[require_authenticated] = lambda: None

    return client


class _RefinementCapture:

    def __init__(self, monkeypatch, main, *, response_data=None):
        self.calls: list[dict] = []
        self._response_data = response_data or {"data": []}
        self._main = main
        self._monkeypatch = monkeypatch

    def install(self):
        capture = self

        class _FakeResponse:
            status_code = 200

            def json(self_inner):
                return capture._response_data

        class _FakeClient:
            def __init__(self_inner, *args, **kwargs):
                pass

            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_):
                pass

            async def post(self_inner, url, *, json=None, **kw):
                capture.calls.append({"url": url, "json": json})
                return _FakeResponse()

            async def get(self_inner, url, **kw):
                return _FakeResponse()

        import httpx
        self._monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        return self


def test_single_value_uses_placeholder_not_interpolation(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"revenue_manager": "Garcia"}, "columns": ["revenue_manager"]},
    )
    assert resp.status_code == 200
    assert cap.calls, "refinement was never called"
    args = cap.calls[0]["json"]["args"]
    sql: str = args["sql"]
    params: list = args.get("params", [])

    assert "Garcia" not in sql, f"Value leaked into SQL: {sql!r}"
    assert "Garcia" in params, f"Value missing from params: {params}"
    assert "?" in sql, f"No placeholder in SQL: {sql!r}"


def test_sql_injection_attempt_in_value_is_neutralised(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    injection = "x' OR 1=1 --"
    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"col": injection}, "columns": ["col"]},
    )
    assert resp.status_code == 200
    args = cap.calls[0]["json"]["args"]
    sql: str = args["sql"]
    params: list = args.get("params", [])

    assert injection not in sql, f"Injection payload leaked into SQL: {sql!r}"
    assert injection in params


def test_single_quote_in_value(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"cliente": "O'Reilly"}, "columns": ["cliente"]},
    )
    assert resp.status_code == 200
    args = cap.calls[0]["json"]["args"]
    params: list = args.get("params", [])
    assert "O'Reilly" in params


def test_backslash_in_value(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"col": "back\\slash"}, "columns": ["col"]},
    )
    assert resp.status_code == 200
    params = cap.calls[0]["json"]["args"].get("params", [])
    assert "back\\slash" in params


def test_in_list_with_multiple_values(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"region": ["Norte", "Sur", "Este"]}, "columns": ["region"]},
    )
    assert resp.status_code == 200
    args = cap.calls[0]["json"]["args"]
    sql: str = args["sql"]
    params: list = args.get("params", [])

    assert "Norte" not in sql
    assert "Sur" not in sql
    assert "Este" not in sql
    assert set(["Norte", "Sur", "Este"]).issubset(set(params))
    assert sql.count("?") == 3


def test_filters_must_be_dict(console_main, monkeypatch):
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": ["col", "val"]},
    )
    assert resp.status_code == 400


def test_too_many_filters_rejected(console_main, monkeypatch):
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    filters = {f"col_{i}": "val" for i in range(21)}
    resp = client.post("/api/data/ventas/query", json={"filters": filters})
    assert resp.status_code == 400
    assert "20" in resp.json().get("detail", "")


def test_too_many_values_per_filter_rejected(console_main, monkeypatch):
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"col": [f"v{i}" for i in range(101)]}},
    )
    assert resp.status_code == 400
    assert "100" in resp.json().get("detail", "")


def test_value_too_long_rejected(console_main, monkeypatch):
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"col": "x" * 501}},
    )
    assert resp.status_code == 400
    assert "500" in resp.json().get("detail", "")


def test_invalid_column_name_is_dropped(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {}, "columns": ["valid_col", "bad-col!", "'; DROP TABLE gold_ventas; --"]},
    )
    assert resp.status_code == 200
    sql: str = cap.calls[0]["json"]["args"]["sql"]
    assert "valid_col" in sql
    assert "bad-col!" not in sql
    assert "DROP" not in sql


def test_invalid_dataset_name_rejected(console_main, monkeypatch):
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/bad-dataset!/query",
        json={"filters": {}, "columns": ["*"]},
    )
    assert resp.status_code == 400


def test_user_context_forwarded_to_refinement(console_main, monkeypatch):
    cap = _RefinementCapture(monkeypatch, console_main).install()
    main = console_main
    from app.dependencies import require_authenticated
    main.app.dependency_overrides[require_authenticated] = lambda: None
    client = TestClient(main.app, raise_server_exceptions=True)
    client.cookies.set("mod_session", "fake-session-token")

    resp = client.post(
        "/api/data/ventas/query",
        json={"filters": {"col": "v"}, "columns": ["col"]},
    )
    assert resp.status_code == 200
    assert cap.calls, "refinement was never called"
    args = cap.calls[0]["json"]["args"]
    assert "user_context" in args, "user_context must be forwarded to refinement for RLS"
    uc = args["user_context"]

    assert uc.get("tenant_id") == "t-active", \
        f"active_tenant_id must win over tenant_id; got {uc.get('tenant_id')!r}"
    assert uc.get("workspace_id") == "ws-active", \
        f"active_workspace_id must win over workspace_id; got {uc.get('workspace_id')!r}"
    assert uc.get("id") == 99, f"id must be resolved; got {uc.get('id')!r}"
    assert uc.get("role") == "admin"
    assert uc.get("email") == "test@test.com"
    assert uc.get("project_id") == "proj-1"

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO_ROOT / "console"

SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)
_CSRF = "unit-test-csrf"


def _set_csrf(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", _CSRF)
    return {"X-CSRF-Token": _CSRF}


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _isolate_console_imports():
    _purge_app_modules()
    saved_path = list(sys.path)
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(CONSOLE_DIR))

    sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

    os.environ.setdefault(
        "INTERNAL_API_KEY",
        "test-internal-api-key-do-not-use-in-prod-aaaaaaaaaaaaaaaaa",
    )
    os.environ.setdefault(
        "JWT_SECRET_KEY",
        "test-jwt-key-do-not-use-in-prod-bbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    yield

    sys.path[:] = saved_path
    _purge_app_modules()


class _InjectUserMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        role = request.headers.get("x-test-user-role")
        if role == "none":
            request.state.user = None
        elif role:
            request.state.user = {"id": 1, "email": "u@test", "role": role}
        else:
            request.state.user = None
        return await call_next(request)


def _make_app() -> FastAPI:
    from app.routers import mcp_public  # noqa: WPS433 — late import after sys.path setup

    application = FastAPI()
    application.add_middleware(_InjectUserMiddleware)
    application.include_router(mcp_public.router)
    return application


def test_router_dependency_is_require_admin():
    from app.routers import mcp_public
    from app.dependencies import require_admin, require_authenticated

    dep_funcs = [d.dependency for d in mcp_public.router.dependencies]
    assert require_admin in dep_funcs, (
        "router must require admin role at the dependency level"
    )
    assert require_authenticated not in dep_funcs, (
        "router must NOT fall through require_authenticated — every route "
        "proxies with console's internal key"
    )


def test_mcp_invoke_rejects_unauthenticated():
    client = TestClient(_make_app())
    resp = client.post(
        "/api/mcp/invoke",
        json={"server": "infra", "tool": "postgres_list_tables", "args": {}},
        headers={"x-test-user-role": "none"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("role", ["user", "analyst", "viewer", "workspace_admin"])
def test_mcp_invoke_rejects_regular_user(role):
    client = TestClient(_make_app())
    resp = client.post(
        "/api/mcp/invoke",
        json={"server": "infra", "tool": "postgres_list_tables", "args": {}},
        headers={"x-test-user-role": role},
    )
    assert resp.status_code == 403, (
        f"role {role!r} must NOT be allowed to invoke MCP tools, got "
        f"{resp.status_code}: {resp.text}"
    )


def test_mcp_invoke_accepts_admin():
    from app.services import mcp_registry

    with patch.object(
        mcp_registry,
        "invoke",
        new=AsyncMock(return_value={"ok": True}),
    ):
        client = TestClient(_make_app())
        csrf_headers = _set_csrf(client)
        resp = client.post(
            "/api/mcp/invoke",
            json={"server": "infra", "tool": "postgres_list_tables", "args": {}},
            headers={"x-test-user-role": "admin", **csrf_headers},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"result": {"ok": True}}


def test_mcp_servers_rejects_regular_user():
    client = TestClient(_make_app())
    resp = client.get(
        "/api/mcp/servers",
        headers={"x-test-user-role": "analyst"},
    )
    assert resp.status_code == 403, resp.text


def test_mcp_servers_accepts_admin():
    from app.services import mcp_registry

    with patch.object(
        mcp_registry,
        "list_servers",
        new=AsyncMock(return_value=[]),
    ):
        client = TestClient(_make_app())
        resp = client.get(
            "/api/mcp/servers",
            headers={"x-test-user-role": "admin"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"servers": []}


_ROUTER_ENDPOINTS = [
    ("GET", "/api/mcp/servers", "list_servers"),
    ("POST", "/api/mcp/servers/register", "register"),
    ("GET", "/api/mcp/servers/abc/tools", "list_tools"),
    ("POST", "/api/mcp/servers/abc/invoke", "invoke"),
    ("POST", "/api/mcp/invoke", "invoke"),
    ("POST", "/api/mcp/servers/health-check", "health_check_all"),
    ("DELETE", "/api/mcp/servers/abc", "deregister"),
]


@pytest.mark.parametrize("method,path,mock_attr", _ROUTER_ENDPOINTS)
@pytest.mark.parametrize("role", ["user", "analyst", "viewer", "workspace_admin"])
def test_every_endpoint_rejects_non_admin(method, path, mock_attr, role):
    client = TestClient(_make_app())
    resp = client.request(
        method,
        path,
        headers={"x-test-user-role": role, "Content-Type": "application/json"},
        json={"server": "infra", "tool": "x", "args": {}} if method == "POST" else None,
    )
    assert resp.status_code == 403, (
        f"{method} {path} as {role!r} should 403, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.parametrize("method,path,mock_attr", _ROUTER_ENDPOINTS)
def test_every_endpoint_accepts_admin(method, path, mock_attr):
    from app.services import mcp_registry

    with patch.object(
        mcp_registry,
        mock_attr,
        new=AsyncMock(return_value={"ok": True} if mock_attr != "list_servers" else []),
    ):
        client = TestClient(_make_app())
        csrf_headers = _set_csrf(client)
        resp = client.request(
            method,
            path,
            headers={"x-test-user-role": "admin", "Content-Type": "application/json", **csrf_headers},
            json={"server": "infra", "tool": "x", "args": {}} if method == "POST" else None,
        )
    assert resp.status_code == 200, (
        f"{method} {path} as admin should 200, got {resp.status_code}: {resp.text}"
    )


def test_no_route_in_mcp_public_bypasses_admin():
    from app.routers import mcp_public
    from app.dependencies import require_admin, require_authenticated

    router_dep_funcs = {d.dependency for d in mcp_public.router.dependencies}
    assert require_admin in router_dep_funcs

    for route in mcp_public.router.routes:
        route_dep_funcs = {d.dependency for d in getattr(route, "dependencies", [])}
        assert "/api/mcp" in route.path, (
            f"unexpected non-/api/mcp path on mcp_public router: {route.path}"
        )
        assert require_authenticated not in route_dep_funcs, (
            f"route {route.path} re-introduces require_authenticated"
        )


def test_no_api_mcp_routes_outside_mcp_public_router():
    import ast

    main_path = REPO_ROOT / "console" / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    offenders: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "app"
                and func.attr in {"get", "post", "put", "delete", "patch"}
            ):
                continue
            if not deco.args or not isinstance(deco.args[0], ast.Constant):
                continue
            path = deco.args[0].value
            if isinstance(path, str) and path.startswith("/api/mcp"):
                offenders.append((func.attr.upper(), path, node.lineno))
    assert not offenders, (
        "endpoints under /api/mcp/* declared directly on `app` in "
        f"console/app/main.py (must go through the mcp_public router so "
        f"router-level require_admin applies): {offenders}"
    )

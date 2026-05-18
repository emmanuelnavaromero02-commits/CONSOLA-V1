"""
Sprint v1.34 — audit B2 (P0).

Every route in ``console/app/routers/mcp_public.py`` proxies its call
through ``console.app.services.mcp_registry``, which uses console's
``INTERNAL_API_KEY`` header to authenticate with downstream MCP servers
(``mcp-infra``, etc.). Before v1.34 the router required only
``require_authenticated``, so a non-admin authenticated user (rol
``user``, ``analyst``, ``viewer``, ``workspace_admin``) could invoke
arbitrary tools — including ``postgres_execute_query`` against the
operational DB or ``airflow_trigger_dag`` / ``airflow_set_variable`` —
under the console service identity. That's a full privilege-escalation
primitive that, combined with the existing SQLi in
``postgres_get_sample`` (audit B3), lets a non-admin read
``users.password_hash``.

This test suite enforces that the entire ``/api/mcp/*`` router now
requires admin role, end-to-end:

1. The router-level dependency is ``require_admin`` (not
   ``require_authenticated``).
2. ``POST /api/mcp/invoke`` without auth → 401.
3. ``POST /api/mcp/invoke`` as a non-admin user → 403.
4. ``POST /api/mcp/invoke`` as admin → 200 (proxy is reached).
5. ``GET /api/mcp/servers`` (tool listing) is also admin-gated.
"""
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

    # The console package imports asyncpg at module load even though
    # these tests never touch Postgres. Stub it out before import.
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
    """Set ``request.state.user`` from the ``X-Test-User-Role`` header.

    The production middleware (``auth_middleware`` in
    ``console/app/main.py``) populates ``request.state.user`` after
    validating session cookies or JWT bearer tokens. Replicating that
    surface in a unit test is overkill — we just need ``require_admin``
    to see the same shape it would see at runtime.
    """

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


# ── Router shape ────────────────────────────────────────────────────────────

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


# ── Behavior: /api/mcp/invoke ────────────────────────────────────────────────

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


# ── Behavior: /api/mcp/servers (tool listing) ────────────────────────────────

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


# ── Behavior: every route in the router is admin-gated ──────────────────────

# (method, path, mock_attr) tuples for every endpoint the router exposes.
# Sprint v1.34: the original audit covered ``/invoke`` and ``/servers``
# only — this matrix locks down the other five so a future contributor
# adding ``dependencies=[Depends(require_authenticated)]`` on any single
# route triggers a failing test instead of shipping silently.
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
    """Every route on mcp_public must reject every non-admin role."""
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
    """Every route on mcp_public must accept admin and reach the proxy."""
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
    """Defense-in-depth: a future contributor must not add a route that
    re-introduces ``require_authenticated`` for an MCP-public endpoint."""
    from app.routers import mcp_public
    from app.dependencies import require_admin, require_authenticated

    router_dep_funcs = {d.dependency for d in mcp_public.router.dependencies}
    assert require_admin in router_dep_funcs

    for route in mcp_public.router.routes:
        route_dep_funcs = {d.dependency for d in getattr(route, "dependencies", [])}
        assert "/api/mcp" in route.path, (
            f"unexpected non-/api/mcp path on mcp_public router: {route.path}"
        )
        # Routes themselves must not lower the bar with require_authenticated.
        assert require_authenticated not in route_dep_funcs, (
            f"route {route.path} re-introduces require_authenticated"
        )


def test_no_api_mcp_routes_outside_mcp_public_router():
    """Hallazgo 5 del auditor: catch the case where someone adds
    ``@app.post('/api/mcp/secret')`` directly in main.py, bypassing the
    router-level ``require_admin``.

    Implemented as a static AST scan of console/app/main.py so it runs
    without importing the full console (which pulls in anthropic/gemini
    SDKs that the test environment may not have). Any FastAPI decorator
    whose first positional argument string starts with ``/api/mcp`` and
    isn't already inside the mcp_public router is an offender."""
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
            # We're looking for @app.<method>("/api/mcp/...")
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

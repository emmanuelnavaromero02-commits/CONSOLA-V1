from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-do-not-use-in-prod-aaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-key-do-not-use-in-prod-bbbbbbbbbbbbbbbbbbbbbbbbb")

from app.routers import mcp, mcp_public  # noqa: E402
from app.dependencies import require_admin, require_authenticated  # noqa: E402
from app.services import mcp_registry as _mcp_registry  # noqa: E402
from app.services.auth import verify_internal_api_key  # noqa: E402


class _InjectUserMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        role = request.headers.get("x-test-user-role")
        if role and role != "none":
            request.state.user = {"id": 1, "email": "u@test", "role": role}
        else:
            request.state.user = None
        return await call_next(request)


def _make_app(with_user_middleware: bool = False) -> FastAPI:
    application = FastAPI()
    if with_user_middleware:
        application.add_middleware(_InjectUserMiddleware)
    application.include_router(mcp.router)
    application.include_router(mcp_public.router)
    return application


def test_mcp_internal_router_prefix_and_dep():
    assert mcp.router.prefix == "/internal/mcp"
    dep_funcs = [d.dependency for d in mcp.router.dependencies]
    assert verify_internal_api_key in dep_funcs


def test_mcp_public_router_prefix_and_dep():
    assert mcp_public.router.prefix == "/api/mcp"
    dep_funcs = [d.dependency for d in mcp_public.router.dependencies]
    assert require_admin in dep_funcs
    assert require_authenticated not in dep_funcs


def test_routers_expose_same_endpoints():
    def shape(r):
        return sorted(
            (tuple(sorted(route.methods or ())), route.path[len(r.prefix):])
            for route in r.routes
        )
    assert shape(mcp.router) == shape(mcp_public.router)


def test_internal_mcp_without_header_returns_403():
    client = TestClient(_make_app())
    response = client.get("/internal/mcp/servers")
    assert response.status_code == 403


def test_internal_mcp_with_valid_header_returns_200():
    client = TestClient(_make_app())
    key = os.environ["INTERNAL_API_KEY"]
    with patch.object(_mcp_registry, "list_servers", new=AsyncMock(return_value=[])):
        response = client.get(
            "/internal/mcp/servers",
            headers={"x-api-key": key, "x-internal-service": "airflow"},
        )
    assert response.status_code == 200, response.text
    assert response.json() == {"servers": []}


def test_public_mcp_without_session_returns_401():
    client = TestClient(_make_app(with_user_middleware=True))
    response = client.get("/api/mcp/servers", headers={"x-test-user-role": "none"})
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["user", "analyst", "viewer", "workspace_admin"])
def test_public_mcp_with_non_admin_returns_403(role):
    client = TestClient(_make_app(with_user_middleware=True))
    response = client.get("/api/mcp/servers", headers={"x-test-user-role": role})
    assert response.status_code == 403, response.text


def test_public_mcp_with_admin_returns_200():
    client = TestClient(_make_app(with_user_middleware=True))
    with patch.object(_mcp_registry, "list_servers", new=AsyncMock(return_value=[])):
        response = client.get("/api/mcp/servers", headers={"x-test-user-role": "admin"})
    assert response.status_code == 200, response.text
    assert response.json() == {"servers": []}

"""Verifica que /api/mcp/* exige sesión + admin y /internal/mcp/* exige header interno.

Fase 1 del sprint v1.0: split del router /mcp en dos:
  - /internal/mcp/* (mcp.router)         → verify_internal_api_key
  - /api/mcp/*      (mcp_public.router)  → require_authenticated

Sprint v1.34 (audit B2 P0): /api/mcp/* now requires ``require_admin``
because every route proxies through console's INTERNAL_API_KEY into
mcp-infra — a non-admin authenticated user reaching ``/invoke`` could
execute ``postgres_execute_query`` against the operational DB. The
dependency was tightened from ``require_authenticated`` to
``require_admin``.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

# Static-only test: stub asyncpg before importing dependencies/auth so the
# import side effects don't try to connect to Postgres.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-do-not-use-in-prod-aaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-key-do-not-use-in-prod-bbbbbbbbbbbbbbbbbbbbbbbbb")

from app.routers import mcp, mcp_public  # noqa: E402
from app.dependencies import require_admin, require_authenticated  # noqa: E402
from app.services import mcp_registry as _mcp_registry  # noqa: E402
from app.services.auth import verify_internal_api_key  # noqa: E402


class _InjectUserMiddleware(BaseHTTPMiddleware):
    """Populate ``request.state.user`` from ``x-test-user-role`` header.

    The production middleware does this after validating session cookies
    or JWT bearer tokens. ``require_admin`` reads ``request.state.user``
    directly, so we replicate just that surface in unit tests.
    """

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


# ── Router shape ────────────────────────────────────────────────────────────

def test_mcp_internal_router_prefix_and_dep():
    assert mcp.router.prefix == "/internal/mcp"
    dep_funcs = [d.dependency for d in mcp.router.dependencies]
    assert verify_internal_api_key in dep_funcs


def test_mcp_public_router_prefix_and_dep():
    assert mcp_public.router.prefix == "/api/mcp"
    dep_funcs = [d.dependency for d in mcp_public.router.dependencies]
    # v1.34: tightened from require_authenticated to require_admin.
    assert require_admin in dep_funcs
    assert require_authenticated not in dep_funcs


def test_routers_expose_same_endpoints():
    """Both routers must expose the same set of (method, path) so that the
    UI proxy and internal callers see one shape."""
    def shape(r):
        return sorted(
            (tuple(sorted(route.methods or ())), route.path[len(r.prefix):])
            for route in r.routes
        )
    assert shape(mcp.router) == shape(mcp_public.router)


# ── Behavior: /internal/mcp/* ───────────────────────────────────────────────

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


# ── Behavior: /api/mcp/* ────────────────────────────────────────────────────

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

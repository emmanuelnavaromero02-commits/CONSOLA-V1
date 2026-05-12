"""Verifica que /api/mcp/* exige sesión y /internal/mcp/* exige header interno.

Fase 1 del sprint v1.0: split del router /mcp en dos:
  - /internal/mcp/* (mcp.router)         → verify_internal_api_key
  - /api/mcp/*      (mcp_public.router)  → require_authenticated
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Static-only test: stub asyncpg before importing dependencies/auth so the
# import side effects don't try to connect to Postgres.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-do-not-use-in-prod-aaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-key-do-not-use-in-prod-bbbbbbbbbbbbbbbbbbbbbbbbb")

from app.routers import mcp, mcp_public  # noqa: E402
from app.dependencies import require_authenticated  # noqa: E402
from app.services import mcp_registry as _mcp_registry  # noqa: E402
from app.services.auth import verify_internal_api_key  # noqa: E402


def _make_app() -> FastAPI:
    application = FastAPI()
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
    assert require_authenticated in dep_funcs


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
    client = TestClient(_make_app())
    response = client.get("/api/mcp/servers")
    assert response.status_code == 401


def test_public_mcp_with_session_returns_200():
    app = _make_app()
    app.dependency_overrides[require_authenticated] = lambda: {
        "id": 1, "role": "admin", "email": "admin@example.com",
    }
    client = TestClient(app)
    with patch.object(_mcp_registry, "list_servers", new=AsyncMock(return_value=[])):
        response = client.get("/api/mcp/servers")
    assert response.status_code == 200, response.text
    assert response.json() == {"servers": []}

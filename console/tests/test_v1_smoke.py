"""Smoke test del sprint v1.0 — los endpoints principales rechazan
correctamente requests no autenticados.

Importa la app real (no construye una FastAPI ad-hoc) para garantizar
que el middleware, los includes de routers y las dependencias estén
todos cableados como en producción.
"""
from __future__ import annotations

import os
import sys
import types
from importlib import import_module
from unittest.mock import Mock

import pytest

# Stub asyncpg before importing app so module-level imports don't fail.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "smokev1internalkeyaaaaaaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "smokev1jwtsecretbbbbbbbbbbbbbbbbbbbbbbbb")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    auth_module = sys.modules.get("app.services.auth")
    if isinstance(auth_module, Mock) or getattr(auth_module, "__name__", "") == "stub":
        sys.modules.pop("app.services.auth", None)
    services_pkg = import_module("app.services")
    auth_module = import_module("app.services.auth")
    setattr(services_pkg, "auth", auth_module)

    routers_pkg = import_module("app.routers")
    for attr in ("mcp", "settings_internal"):
        if hasattr(routers_pkg, attr):
            delattr(routers_pkg, attr)
    for module_name in ("app.main", "app.routers.mcp", "app.routers.settings_internal"):
        sys.modules.pop(module_name, None)
    from app.main import app
    return TestClient(app)


def test_system_info_requires_auth(client):
    r = client.get("/api/system/info")
    assert r.status_code in (401, 403, 307)


def test_settings_requires_auth(client):
    r = client.get("/api/settings")
    assert r.status_code in (401, 403, 307)


def test_mcp_public_requires_auth(client):
    r = client.get("/api/mcp/servers")
    assert r.status_code in (401, 403, 307)


def test_operations_health_requires_auth(client):
    r = client.get("/api/operations/health")
    assert r.status_code in (401, 403, 307)


def test_internal_mcp_requires_header(client):
    """Internal route: sin header → 403 (Fase 3 fix middleware bypass)."""
    r = client.get("/internal/mcp/servers")
    assert r.status_code == 403

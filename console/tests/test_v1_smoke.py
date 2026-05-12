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

import pytest

# Stub asyncpg before importing app so module-level imports don't fail.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "smoke-test-key-do-not-use-aaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "smoke-test-jwt-key-do-not-use-bbbbbbbbbbbbbbb")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
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

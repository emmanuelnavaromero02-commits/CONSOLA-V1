from __future__ import annotations

import os
import sys
import types
from importlib import import_module

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "route-registry-internal-key")
os.environ.setdefault("JWT_SECRET_KEY", "route-registry-jwt-secret")
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))


def _load_app():
    for module_name in ("app.main",):
        sys.modules.pop(module_name, None)
    return import_module("app.main").app


def test_all_fastapi_routes_have_surface_classification():
    from app.route_surface_registry import classify_route_surface

    app = _load_app()
    missing = sorted(
        {
            route.path
            for route in app.routes
            if getattr(route, "path", None)
            and classify_route_surface(route.path) is None
        }
    )

    assert missing == []


def test_sensitive_prefixes_are_not_frontend_surfaces():
    from app.route_surface_registry import classify_route_surface

    assert classify_route_surface("/internal/intelligence/monte-carlo/run") == "internal"
    assert classify_route_surface("/monitoring/mcp/tools") == "internal"
    assert classify_route_surface("/studio_ops/mcp/invoke") == "internal"
    assert classify_route_surface("/api/v1/intelligence/runs") == "legacy"
    assert classify_route_surface("/api/intelligence/runs") == "frontend"

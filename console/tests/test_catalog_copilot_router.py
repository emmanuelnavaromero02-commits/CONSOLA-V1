from __future__ import annotations

import ast
import os
from importlib import import_module
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "test")

ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "catalog_copilot.py"


def _decorators() -> dict[str, list[str]]:
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and isinstance(deco.func.value, ast.Name)
                and deco.func.value.id == "router"
            ):
                continue
            path = deco.args[0].value
            deps: list[str] = []
            for keyword in deco.keywords:
                if keyword.arg != "dependencies":
                    continue
                for element in keyword.value.elts:
                    inner = element.args[0]
                    if isinstance(inner, ast.Name):
                        deps.append(inner.id)
                    elif isinstance(inner, ast.Call):
                        deps.append(inner.func.id)
                        if inner.args and isinstance(inner.args[0], ast.Constant):
                            deps.append(f"{inner.func.id}:{inner.args[0].value}")
            found[f"{deco.func.attr.upper()} {path}"] = deps
    return found


def test_router_declares_absolute_paths_without_a_prefix():
    source = ROUTER.read_text(encoding="utf-8")
    assert 'router = APIRouter(tags=["catalog"])' in source
    assert set(_decorators()) == {
        "POST /api/catalog/auto-profile",
        "POST /api/catalog/relationships/reject",
    }


def test_every_mutation_requires_csrf_and_the_right_permission():
    routes = _decorators()
    auto = routes["POST /api/catalog/auto-profile"]
    assert "require_csrf" in auto
    assert "require_permission:datasets.read" in auto
    reject = routes["POST /api/catalog/relationships/reject"]
    assert "require_csrf" in reject
    assert "require_permission:datasets.write" in reject
    assert "require_any_role" in reject


def test_routes_are_mounted_and_classified_as_frontend_surfaces():
    app = import_module("app.main").app
    from app.route_surface_registry import classify_route_surface

    mounted = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
    }
    assert ("/api/catalog/auto-profile", "POST") in mounted
    assert ("/api/catalog/relationships/reject", "POST") in mounted
    assert classify_route_surface("/api/catalog/auto-profile") == "frontend"
    assert classify_route_surface("/api/catalog/relationships/reject") == "frontend"


def test_catalog_prefix_goes_through_the_rbac_dependency_gate():
    main = import_module("app.main")
    assert "/api/catalog" in main._RBAC_DEPENDENCY_PREFIXES
    assert main._uses_rbac_dependency("/api/catalog/auto-profile")
    assert main._uses_rbac_dependency("/api/catalog/relationships/reject")


def test_audited_route_source_includes_the_router():
    import sys

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    try:
        from tests.console_route_source import console_route_source
    finally:
        sys.path.remove(str(repo))
    source = console_route_source()
    assert '@app.post(\n    "/api/catalog/auto-profile"' in source
    assert '@app.post(\n    "/api/catalog/relationships/reject"' in source


@pytest.mark.asyncio
async def test_router_uses_the_signed_refinement_transport(monkeypatch):
    router = import_module("app.routers.catalog_copilot")
    captured = {}

    async def fake_impl(tool, args, **kwargs):
        captured["tool"] = tool
        captured["kwargs"] = kwargs
        return {"status": "idle", "processed": 0, "pending": 0, "stale": 0}

    monkeypatch.setattr(router, "refinement_invoke_impl", fake_impl)
    result = await router._refinement_invoke("auto_catalog", {}, timeout=10, user={"id": 1})
    assert result["status"] == "idle"
    kwargs = captured["kwargs"]
    assert kwargs["hdr_for"] is router._hdr_for
    assert kwargs["mcp_payload"] is router.mcp_payload
    assert kwargs["timeout"] == 10

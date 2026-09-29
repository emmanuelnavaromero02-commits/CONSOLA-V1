from __future__ import annotations

import importlib

import pytest

from app.services.mcp_gateway import catalog


def _required_permissions(dependant) -> set[str]:
    found: set[str] = set()
    stack = list(dependant.dependencies)
    while stack:
        current = stack.pop()
        permission = getattr(current.call, "required_permission", None)
        if permission:
            found.add(str(permission))
        stack.extend(current.dependencies)
    return found


def _route(app, method: str, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(
    "action",
    [action for action in catalog.ACTIONS if action.underlying is not None],
    ids=lambda action: action.name,
)
def test_gateway_permission_matches_the_underlying_console_route(action):
    app = importlib.import_module("app.main").app
    method, path = action.underlying
    route = _route(app, method, path)
    assert action.permission in _required_permissions(route.dependant), (action.name, path)


def test_only_context_and_app_creation_lack_an_http_twin():
    assert {action.name for action in catalog.ACTIONS if action.underlying is None} == {
        "consultar_contexto",
        "crear_app_analitica",
    }
    assert catalog.get_action("consultar_contexto").permission is None
    assert catalog.get_action("crear_app_analitica").permission == "apps.write"


@pytest.mark.asyncio
async def test_app_creation_enforces_the_same_permission_downstream():
    from app.services import app_forge

    viewer = {
        "id": 1,
        "role": "user",
        "workspace_role": "viewer",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    with pytest.raises(app_forge.AppForgeError):
        await app_forge.generate_and_publish_app(
            viewer, objective="Revisar margen por cliente", datasets=["gold_margen"]
        )

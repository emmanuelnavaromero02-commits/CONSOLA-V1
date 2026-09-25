from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.console_route_source import console_route_source

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"


PUBLIC_ROUTE_ALLOWLIST = {
    ("/auth/login", "POST"),
    ("/api/auth/login", "POST"),
    ("/auth/refresh", "POST"),
    ("/auth/logout", "POST"),
    ("/auth/forgot-password", "POST"),
    ("/auth/reset-password", "POST"),
    ("/auth/activate", "POST"),
    ("/login", "GET"),
    ("/me", "GET"),
    ("/forgot-password", "GET"),
    ("/reset-password", "GET"),
    ("/activate", "GET"),
    ("/healthz", "GET"),
    ("/readyz", "GET"),
    ("/api/config", "GET"),
    ("/favicon.ico", "GET"),
    ("/api/csrf", "GET"),
    ("/vpn-config/{token}", "GET"),
}


KNOWN_GAPS_DEFERRED_TO_V1_22: set[tuple[str, str]] = set()


AUTH_SURFACE_ALLOWLIST = {
    ("/auth/me", "GET"),
    ("/auth/activate/info", "GET"),
    ("/auth/reset/info", "GET"),
    ("/auth/me-jwt", "GET"),
}


AUTH_DEPENDS = (
    "require_authenticated",
    "require_csrf",
    "require_permission",
    "require_any_role",
    "require_admin",
    "require_global_any_role",
    "require_role",
    "require_user",
    "get_current_user",
    "get_current_global_user",
    "get_current_user_dependency",
    "_internal_or_authenticated",
    "verify_internal_api_key",
)


INLINE_AUTH_GATES = {
    ("/apps/{name}/content", "GET"): "_require_app_content_capability",
}


def _route_decorators(tree: ast.Module):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            f = deco.func
            if not (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "app"
                and f.attr in {"get", "post", "put", "patch", "delete", "head"}
            ):
                continue
            if not deco.args or not isinstance(deco.args[0], ast.Constant):
                continue
            path = deco.args[0].value
            if not isinstance(path, str):
                continue
            yield path, f.attr.upper(), deco, node


def _depends_call_name(call: ast.AST) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    if not (isinstance(call.func, ast.Name) and call.func.id == "Depends"):
        return None
    if not call.args:
        return None
    inner = call.args[0]
    if isinstance(inner, ast.Name):
        return inner.id
    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
        return inner.func.id
    return None


def _decorator_auth_deps(deco: ast.Call) -> list[str]:
    found: list[str] = []
    for kw in deco.keywords:
        if kw.arg != "dependencies":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            continue
        for elt in kw.value.elts:
            name = _depends_call_name(elt)
            if name and name in AUTH_DEPENDS:
                found.append(name)
    return found


def _param_auth_deps(func: ast.AST) -> list[str]:
    args = getattr(func, "args", None)
    if args is None:
        return []
    defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    found: list[str] = []
    for default in defaults:
        name = _depends_call_name(default)
        if name and name in AUTH_DEPENDS:
            found.append(name)
    return found


def _inline_auth_gate(path: str, method: str, func: ast.AST) -> str | None:
    required = INLINE_AUTH_GATES.get((path, method))
    if required is None:
        return None
    body = getattr(func, "body", [])
    first = next(
        (
            statement
            for statement in body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ),
        None,
    )
    if not isinstance(first, (ast.Assign, ast.AnnAssign, ast.Expr)):
        return None
    value = first.value
    if not isinstance(value, ast.Await) or not isinstance(value.value, ast.Call):
        return None
    called = value.value.func
    if isinstance(called, ast.Name) and called.id == required:
        return required
    return None


def _route_has_auth(path: str, method: str, deco: ast.Call, func: ast.AST) -> bool:
    return (
        bool(_decorator_auth_deps(deco))
        or bool(_param_auth_deps(func))
        or bool(_inline_auth_gate(path, method, func))
    )


def _decorator_has_auth(deco: ast.Call) -> bool:
    return bool(_decorator_auth_deps(deco))


@pytest.fixture(scope="module")
def console_routes():
    tree = ast.parse(console_route_source())
    return list(_route_decorators(tree))


def test_monitoring_mcp_invoke_requires_auth(console_routes):
    matches = [
        (p, m, d, f)
        for p, m, d, f in console_routes
        if p == "/monitoring/mcp/invoke" and m == "POST"
    ]
    assert matches, "POST /monitoring/mcp/invoke is no longer declared on app"
    path, method, deco, func = matches[0]
    assert _route_has_auth(path, method, deco, func), (
        f"POST {path} must declare an auth dep (decorator-level or "
        f"parameter-level Depends)."
    )


def test_app_content_uses_inline_capability_gate_not_public_allowlist(console_routes):
    route = ("/apps/{name}/content", "GET")
    assert route not in PUBLIC_ROUTE_ALLOWLIST
    assert route not in AUTH_SURFACE_ALLOWLIST
    matches = [
        (path, method, func)
        for path, method, _deco, func in console_routes
        if (path, method) == route
    ]
    assert len(matches) == 2, "main and the v1 router must expose the same gated door"
    assert all(
        _inline_auth_gate(path, method, func) == INLINE_AUTH_GATES[route]
        for path, method, func in matches
    )


def test_no_unexpected_public_endpoint(console_routes):
    bad = []
    for path, method, deco, func in console_routes:
        if (path, method) in PUBLIC_ROUTE_ALLOWLIST:
            continue
        if (path, method) in AUTH_SURFACE_ALLOWLIST:
            continue
        if (path, method) in KNOWN_GAPS_DEFERRED_TO_V1_22:
            continue
        if _route_has_auth(path, method, deco, func):
            continue
        bad.append(f"{method} {path}")
    if bad:
        pytest.fail(
            "NEW unauthenticated route detected (not in any allowlist). "
            "Add `dependencies=[Depends(require_*)]` to the decorator "
            "or `user: dict = Depends(require_authenticated)` to the "
            "handler signature:\n  " + "\n  ".join(sorted(bad))
        )


def test_v22_gap_list_only_contains_gaps_that_still_exist(console_routes):
    stale = []
    fixed = []
    actual_routes = {
        (p, m): _route_has_auth(p, m, d, f) for p, m, d, f in console_routes
    }
    for entry in sorted(KNOWN_GAPS_DEFERRED_TO_V1_22):
        if entry not in actual_routes:
            stale.append(f"{entry[1]} {entry[0]}  (route no longer exists)")
        elif actual_routes[entry]:
            fixed.append(f"{entry[1]} {entry[0]}  (now has auth — remove from list)")
    msgs = []
    if stale:
        msgs.append(
            "Stale entries in KNOWN_GAPS_DEFERRED_TO_V1_22:\n  " + "\n  ".join(stale)
        )
    if fixed:
        msgs.append(
            "Already-fixed entries still in KNOWN_GAPS_DEFERRED_TO_V1_22:\n  "
            + "\n  ".join(fixed)
        )
    assert not msgs, "\n\n".join(msgs)


def test_v22_gap_list_is_empty():
    assert KNOWN_GAPS_DEFERRED_TO_V1_22 == set(), (
        f"KNOWN_GAPS_DEFERRED_TO_V1_22 is no longer empty: "
        f"{sorted(KNOWN_GAPS_DEFERRED_TO_V1_22)}. Add the auth dep to "
        f"the route(s) instead of adding entries here."
    )

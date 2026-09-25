from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.console_route_source import console_route_source


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


V22_CSRF_FIXES = frozenset({
    ("/assistant/chat",                             "POST"),
    ("/api/bronze/query",                           "POST"),
    ("/studio/chat",                                "POST"),
    ("/studio/chat/stream",                         "POST"),
    ("/studio_ops/mcp/invoke",                      "POST"),
    ("/api/decisions",                              "POST"),
    ("/api/decisions/{decision_id}",                "PATCH"),
    ("/api/decisions/{decision_id}",                "DELETE"),
    ("/api/decisions/{decision_id}/actions",        "POST"),
    ("/api/admin/users",                            "POST"),
    ("/api/admin/users/{user_id}",                  "PATCH"),
    ("/api/admin/users/{user_id}",                  "DELETE"),
    ("/api/admin/users/invite",                     "POST"),
    ("/api/admin/users/{user_id}/reinvite",         "POST"),
    ("/api/admin/users/{user_id}/send-reset",       "POST"),
    ("/auth/refresh",                               "POST"),
    ("/monitoring/invoke",                          "POST"),
    ("/api/dags/parse",                             "POST"),
})


CSRF_EXEMPT_BY_DESIGN = frozenset({
    ("/auth/login",         "POST"),
    ("/api/auth/login",     "POST"),
    ("/auth/activate",      "POST"),
    ("/api/agents/{agent_id}/invoke/scheduled", "POST"),
})


KNOWN_CSRF_GAPS_FOR_LATER = frozenset({
    ("/api/data/{dataset}/query",                                           "POST"),
    ("/monitoring/mcp/invoke",                                              "POST"),
})


def _route_decorators(tree: ast.Module):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            f = deco.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "app"
                    and f.attr in {"get", "post", "put", "patch", "delete", "head"}):
                continue
            if not deco.args or not isinstance(deco.args[0], ast.Constant):
                continue
            path = deco.args[0].value
            if not isinstance(path, str):
                continue
            yield path, f.attr.upper(), deco


def _decorator_deps(deco: ast.Call) -> list[str]:
    found: list[str] = []
    for kw in deco.keywords:
        if kw.arg != "dependencies":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            continue
        for elt in kw.value.elts:
            if not isinstance(elt, ast.Call):
                continue
            if not (isinstance(elt.func, ast.Name) and elt.func.id == "Depends"):
                continue
            if not elt.args:
                continue
            inner = elt.args[0]
            if isinstance(inner, ast.Name):
                found.append(inner.id)
            elif isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                found.append(inner.func.id)
    return found


def _has_csrf(deco: ast.Call) -> bool:
    return "require_csrf" in _decorator_deps(deco)


@pytest.fixture(scope="module")
def console_state_changing_routes():
    tree = ast.parse(console_route_source())
    routes = []
    for path, method, deco in _route_decorators(tree):
        if method in STATE_CHANGING_METHODS:
            routes.append((path, method, deco))
    return routes


@pytest.mark.parametrize("path,method", sorted(V22_CSRF_FIXES))
def test_v22_csrf_fixes_are_in_place(console_state_changing_routes, path, method):
    matches = [
        (p, m, d) for p, m, d in console_state_changing_routes
        if (p, m) == (path, method)
    ]
    assert matches, (
        f"v1.22 fix lost: {method} {path} is no longer declared on app"
    )
    _, _, deco = matches[0]
    assert _has_csrf(deco), (
        f"{method} {path} lost its CSRF dep. v1.22 wins must persist — "
        f"re-add `dependencies=[Depends(require_csrf)]` to the route."
    )


def test_no_state_changing_route_is_csrf_free(console_state_changing_routes):
    bad = []
    for path, method, deco in console_state_changing_routes:
        if _has_csrf(deco):
            continue
        if (path, method) in CSRF_EXEMPT_BY_DESIGN:
            continue
        if (path, method) in KNOWN_CSRF_GAPS_FOR_LATER:
            continue
        bad.append(f"{method} {path}")
    if bad:
        pytest.fail(
            "NEW state-changing route without CSRF (not in any allowlist):\n  "
            + "\n  ".join(sorted(bad))
            + "\nAdd `dependencies=[Depends(require_csrf)]` to the decorator."
        )


def test_known_csrf_gap_list_only_contains_gaps_that_still_exist(console_state_changing_routes):
    actual = {(p, m): _has_csrf(d) for p, m, d in console_state_changing_routes}
    stale = []
    fixed = []
    for entry in sorted(KNOWN_CSRF_GAPS_FOR_LATER):
        if entry not in actual:
            stale.append(f"{entry[1]} {entry[0]}  (route no longer exists)")
        elif actual[entry]:
            fixed.append(f"{entry[1]} {entry[0]}  (now has CSRF — remove from list)")
    msgs = []
    if stale:
        msgs.append(
            "Stale entries in KNOWN_CSRF_GAPS_FOR_LATER:\n  "
            + "\n  ".join(stale)
        )
    if fixed:
        msgs.append(
            "Already-fixed entries still in KNOWN_CSRF_GAPS_FOR_LATER:\n  "
            + "\n  ".join(fixed)
        )
    assert not msgs, "\n\n".join(msgs)


def test_csrf_exempt_routes_have_documented_rationale():
    src = Path(__file__).read_text(encoding="utf-8")
    start = src.index("CSRF_EXEMPT_BY_DESIGN = frozenset({")
    end = src.index("})", start)
    block = src[start:end]
    for path, method in CSRF_EXEMPT_BY_DESIGN:
        assert f'("{path}"' in block, (
            f"CSRF_EXEMPT_BY_DESIGN entry {method} {path} is not "
            f"referenced in the comment block above it. Add a comment "
            f"explaining why this route is legitimately CSRF-exempt."
        )

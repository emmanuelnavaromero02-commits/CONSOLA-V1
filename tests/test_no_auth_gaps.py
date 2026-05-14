"""Sprint v1.21 (F1) — audit guard against unauthenticated state-changing
endpoints in the console.

The auditor flagged `/monitoring/mcp/invoke` for shipping without auth.
This test pins the fix and the explicit allowlist of routes that may
legitimately be public, so a future PR that introduces a new public
endpoint has to take it through code review (by extending the allowlist
here) — not by silently inheriting "no Depends() means no auth".

The test parses console/app/main.py with the ast module instead of
booting FastAPI — this keeps the suite fast and avoids the heavy import
side-effects (DB pools, mcp_registry, logging config) that
console/app/main.py triggers at import.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"


# Routes that are public on purpose. Anything else state-changing or
# read-sensitive needs Depends(require_*). A failing test that complains
# about a new route here is the correct outcome — extend this set with
# justification in code review, don't silently widen it.
PUBLIC_ROUTE_ALLOWLIST = {
    # Auth surface — these endpoints ARE the gateway, so they can't
    # depend on auth being present (chicken-and-egg).
    ("/auth/login",          "POST"),
    ("/auth/refresh",        "POST"),
    ("/auth/logout",         "POST"),
    ("/auth/forgot-password", "POST"),
    ("/auth/reset-password", "POST"),
    ("/auth/activate",       "POST"),
    # GETs on login / change-password forms render the HTML page; the
    # POSTs above are the actual gateways. Auth-gating the page itself
    # would 401 anonymous visitors before they see the login form.
    ("/login",               "GET"),
    ("/me",                  "GET"),
    ("/forgot-password",     "GET"),
    ("/reset-password",      "GET"),
    ("/activate",            "GET"),
    # Liveness / runtime config — non-sensitive.
    ("/healthz",             "GET"),
    ("/api/config",          "GET"),
    ("/favicon.ico",         "GET"),
    # CSRF token endpoint — needs to be reachable before any
    # state-changing form posts, so it can't itself require auth.
    ("/api/csrf",            "GET"),
}


# Sprint v1.21 scope is F1 only — `/monitoring/mcp/invoke`. The auditor
# also flagged many other endpoints; they were deferred to v1.22 (F5/F8)
# per the sprint spec. This list pins what we know is wrong TODAY so:
#   1. A new public route added by accident still fails the test.
#   2. v1.22 progress shrinks this list one entry at a time.
#   3. A reviewer can see what's queued for v1.22 at a glance.
# DO NOT ADD entries here without explicit operator sign-off. Removing
# an entry (by adding auth to the route) is always welcome.
KNOWN_GAPS_DEFERRED_TO_V1_22 = {
    # State-changing endpoints without auth (queued for v1.22 F5/F8):
    ("/api/admin/users",                            "POST"),
    ("/api/admin/users/invite",                     "POST"),
    ("/api/admin/users/{user_id}",                  "PATCH"),
    ("/api/admin/users/{user_id}",                  "DELETE"),
    ("/api/admin/users/{user_id}/reinvite",         "POST"),
    ("/api/admin/users/{user_id}/send-reset",       "POST"),
    ("/api/bronze/query",                           "POST"),
    ("/api/dags/parse",                             "POST"),
    ("/api/decisions",                              "POST"),
    ("/api/decisions/{decision_id}",                "PATCH"),
    ("/api/decisions/{decision_id}",                "DELETE"),
    ("/api/decisions/{decision_id}/actions",        "POST"),
    ("/assistant/chat",                             "POST"),
    ("/monitoring/invoke",                          "POST"),
    ("/studio/chat",                                "POST"),
    ("/studio/chat/stream",                         "POST"),
    ("/studio_ops/mcp/invoke",                      "POST"),
    # Public GETs that may return sensitive data (queued for v1.22):
    ("/auth/me",                                    "GET"),
    ("/auth/me-jwt",                                "GET"),
    ("/auth/me-current",                            "GET"),
    ("/auth/activate/info",                         "GET"),
    ("/auth/reset/info",                            "GET"),
    ("/api/system/info",                            "GET"),
    ("/api/me",                                     "GET"),
    ("/apps/{name}",                                "GET"),
    ("/api/data/{dataset}/options",                 "GET"),
    ("/rag",                                        "GET"),
    ("/monitoring/mcp/tools",                       "GET"),
    ("/studio_ops/mcp/tools",                       "GET"),
    ("/monitoring/tools",                           "GET"),
    ("/api/decisions",                              "GET"),
    ("/api/decisions/{decision_id}",                "GET"),
    ("/api/users",                                  "GET"),
    ("/api/admin/users",                            "GET"),
}


# Decorators that mark a route as authenticated. Order independent.
AUTH_DEPENDS = (
    "require_authenticated",
    "require_csrf",
    "require_permission",
    "require_any_role",
    "require_admin",
    "require_role",
    "verify_internal_api_key",
)


def _route_decorators(tree: ast.Module):
    """Yield (path, method, decorator_ast) for every @app.{method}("path", …)
    decorator we can find at the module level.

    We deliberately ignore router-level mounts (/internal/* lives on a
    sub-router with its own dependencies, asserted by separate tests).
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            f = deco.func
            # Match `app.get(...)`, `app.post(...)`, etc.
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


def _decorator_has_auth(deco: ast.Call) -> bool:
    """Return True if a `dependencies=[Depends(require_*)]` keyword arg
    is present on the route decorator."""
    for kw in deco.keywords:
        if kw.arg != "dependencies":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            continue
        for elt in kw.value.elts:
            # Depends(require_authenticated) — Call where the inner arg
            # is a Name from the AUTH_DEPENDS list, optionally itself a
            # Call (require_permission("foo")).
            if not isinstance(elt, ast.Call):
                continue
            if not (isinstance(elt.func, ast.Name) and elt.func.id == "Depends"):
                continue
            if not elt.args:
                continue
            inner = elt.args[0]
            if isinstance(inner, ast.Name) and inner.id in AUTH_DEPENDS:
                return True
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id in AUTH_DEPENDS:
                return True
    return False


@pytest.fixture(scope="module")
def console_routes():
    tree = ast.parse(CONSOLE_MAIN.read_text(encoding="utf-8"))
    return list(_route_decorators(tree))


def test_monitoring_mcp_invoke_requires_auth(console_routes):
    """The headline of F1: this exact route must declare an auth dep."""
    matches = [
        (p, m, d) for p, m, d in console_routes
        if p == "/monitoring/mcp/invoke" and m == "POST"
    ]
    assert matches, "POST /monitoring/mcp/invoke is no longer declared on app"
    path, method, deco = matches[0]
    assert _decorator_has_auth(deco), (
        f"POST {path} must declare dependencies=[Depends(require_authenticated)] "
        f"(or any require_* dep). v1.21 F1 fix."
    )


def test_no_unexpected_public_endpoint(console_routes):
    """Every route must either declare an auth dep, OR be in the
    legitimate public allowlist, OR be a known v1.22-deferred gap.

    A NEW unauthenticated route triggers this test, because it won't
    match any of the three categories — the right fix for a new route
    is to add the auth dep, not to extend either allowlist. Adding to
    KNOWN_GAPS_DEFERRED_TO_V1_22 requires explicit operator sign-off.
    """
    bad = []
    for path, method, deco in console_routes:
        if (path, method) in PUBLIC_ROUTE_ALLOWLIST:
            continue
        if (path, method) in KNOWN_GAPS_DEFERRED_TO_V1_22:
            continue
        if _decorator_has_auth(deco):
            continue
        bad.append(f"{method} {path}")
    if bad:
        pytest.fail(
            "NEW unauthenticated route detected (not in either allowlist). "
            "Add `dependencies=[Depends(require_*)]` to the route:\n  "
            + "\n  ".join(sorted(bad))
        )


def test_v22_gap_list_only_contains_gaps_that_still_exist(console_routes):
    """When v1.22 lands an auth fix for one of the deferred gaps, the
    fix author must REMOVE that entry from KNOWN_GAPS_DEFERRED_TO_V1_22.
    This test fails if a listed entry is now authenticated — forcing
    the cleanup so the list stays accurate.

    It also fails if a listed entry no longer exists at all (deleted
    route), so dead allowlist entries don't accumulate.
    """
    stale = []
    fixed = []
    actual_routes = {(p, m): _decorator_has_auth(d) for p, m, d in console_routes}
    for entry in sorted(KNOWN_GAPS_DEFERRED_TO_V1_22):
        if entry not in actual_routes:
            stale.append(f"{entry[1]} {entry[0]}  (route no longer exists)")
        elif actual_routes[entry]:
            fixed.append(f"{entry[1]} {entry[0]}  (now has auth — remove from list)")
    msgs = []
    if stale:
        msgs.append("Stale entries in KNOWN_GAPS_DEFERRED_TO_V1_22:\n  " + "\n  ".join(stale))
    if fixed:
        msgs.append("Already-fixed entries still in KNOWN_GAPS_DEFERRED_TO_V1_22:\n  " + "\n  ".join(fixed))
    assert not msgs, "\n\n".join(msgs)

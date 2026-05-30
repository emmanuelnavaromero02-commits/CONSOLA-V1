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

from tests.console_route_source import console_route_source

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
    # Legacy compat alias for /auth/login. Public by gateway design;
    # internally delegates to the same CSRF-protected credential flow.
    ("/api/auth/login",      "POST"),
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
    # Readiness is public by deploy design: load balancers and wait
    # scripts need dependency state before any user session exists.
    ("/readyz",              "GET"),
    ("/api/config",          "GET"),
    ("/favicon.ico",         "GET"),
    # CSRF token endpoint — needs to be reachable before any
    # state-changing form posts, so it can't itself require auth.
    ("/api/csrf",            "GET"),
    # VPN config download is token-protected: the random one-time token in
    # the path is the auth factor sent in the invitation email.
    ("/vpn-config/{token}",   "GET"),
}


# Sprint v1.22 closed the auth gaps v1.21 inherited. This set is empty
# on purpose — any new unauthenticated route fails
# `test_no_unexpected_public_endpoint` immediately. The recon during
# v1.22 also discovered that v1.21's gap list had 25/33 false positives
# (the routes ARE authenticated via parameter-level Depends() but the
# v1.21 test only inspected decorator-level dependencies). The new
# `_route_has_auth()` below checks both.
#
# A new entry here requires explicit operator sign-off. Removing an
# entry (by adding auth to the route) is always welcome.
KNOWN_GAPS_DEFERRED_TO_V1_22: set[tuple[str, str]] = set()


# Auth-surface routes that DON'T return user-scoped data and MUST be
# reachable anonymously by design:
#   - /auth/me           returns {user: null} for anonymous; the SPA
#                        polls this to decide "show login or chrome?"
#   - /auth/activate/info  token-info probe for the activation form
#                          (the token IS the auth factor)
#   - /auth/reset/info     same pattern for password reset
# These were in v1.21's gap list because v1.21 (incorrectly) treated
# every route without a decorator-level dep as missing auth.
AUTH_SURFACE_ALLOWLIST = {
    ("/auth/me",            "GET"),
    ("/auth/activate/info", "GET"),
    ("/auth/reset/info",    "GET"),
    # /auth/me-jwt authenticates inline (reads the Authorization
    # header directly and raises 401 if missing or invalid), so the
    # AST visitor can't see a `Depends()`. Functionally gated. A
    # future cleanup can refactor it to a Depends and remove this
    # entry from the allowlist.
    ("/auth/me-jwt",        "GET"),
}


# Dependencies that mark a route as authenticated. Order independent.
# v1.22: added the aliases v1.21 missed:
#   - get_current_user_dependency (alias used in /auth/me-current)
#   - require_user                  (alias in dependencies.py)
#   - current_user                  (returns None for anon; protects only
#                                    when the route handler 401s itself —
#                                    see /auth/me-jwt which does that)
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


def _route_decorators(tree: ast.Module):
    """Yield (path, method, decorator_ast, function_ast) for every
    @app.{method}("path", …) at the module level.

    v1.22: now also yields the function node so callers can inspect
    parameter-level Depends(). v1.21 only yielded the decorator and
    missed routes whose auth lives on the handler signature.
    """
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
            yield path, f.attr.upper(), deco, node


def _depends_call_name(call: ast.AST) -> str | None:
    """If ``call`` is ``Depends(name)`` or ``Depends(name(...))``, return
    the inner name. Otherwise None."""
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
    """Return the list of auth-dep names found in
    `dependencies=[Depends(require_*)]` on the route decorator."""
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
    """Return the list of auth-dep names found in parameter defaults:
    `param: T = Depends(require_*)`. Catches the v1.21 false-positive
    case where the handler is authed via signature, not decorator."""
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


def _route_has_auth(deco: ast.Call, func: ast.AST) -> bool:
    """A route is authenticated if it has any auth dep either at the
    decorator level OR at the parameter level."""
    return bool(_decorator_auth_deps(deco)) or bool(_param_auth_deps(func))


def _decorator_has_auth(deco: ast.Call) -> bool:
    """Legacy v1.21 helper — decorator-level only. Kept for back-compat
    with the v1.21 monitoring/mcp/invoke pin (which uses the decorator
    form). New tests should call `_route_has_auth(deco, func)` instead."""
    return bool(_decorator_auth_deps(deco))


@pytest.fixture(scope="module")
def console_routes():
    tree = ast.parse(console_route_source())
    return list(_route_decorators(tree))


def test_monitoring_mcp_invoke_requires_auth(console_routes):
    """The headline of F1: this exact route must declare an auth dep.

    Pinned in decorator form because v1.21 explicitly added it there;
    a future refactor to parameter-level Depends() is also acceptable
    (the test then continues to pass via _route_has_auth)."""
    matches = [
        (p, m, d, f) for p, m, d, f in console_routes
        if p == "/monitoring/mcp/invoke" and m == "POST"
    ]
    assert matches, "POST /monitoring/mcp/invoke is no longer declared on app"
    path, method, deco, func = matches[0]
    assert _route_has_auth(deco, func), (
        f"POST {path} must declare an auth dep (decorator-level or "
        f"parameter-level Depends)."
    )


def test_no_unexpected_public_endpoint(console_routes):
    """Every route must either:
      1. declare an auth dep (decorator OR parameter), OR
      2. be in PUBLIC_ROUTE_ALLOWLIST (legitimate public), OR
      3. be in AUTH_SURFACE_ALLOWLIST (token-probe / SPA polling).

    A NEW unauthenticated route triggers this test. The right fix for a
    new route is to add the auth dep — not to extend either allowlist.
    Extending either allowlist requires explicit operator sign-off and
    an inline comment justifying the exception.

    v1.22: KNOWN_GAPS_DEFERRED_TO_V1_22 was drained to empty. If you
    catch yourself wanting to add a route there to silence this test,
    add the auth dep instead — that's why this list exists.
    """
    bad = []
    for path, method, deco, func in console_routes:
        if (path, method) in PUBLIC_ROUTE_ALLOWLIST:
            continue
        if (path, method) in AUTH_SURFACE_ALLOWLIST:
            continue
        if (path, method) in KNOWN_GAPS_DEFERRED_TO_V1_22:
            continue
        if _route_has_auth(deco, func):
            continue
        bad.append(f"{method} {path}")
    if bad:
        pytest.fail(
            "NEW unauthenticated route detected (not in any allowlist). "
            "Add `dependencies=[Depends(require_*)]` to the decorator "
            "or `user: dict = Depends(require_authenticated)` to the "
            "handler signature:\n  "
            + "\n  ".join(sorted(bad))
        )


def test_v22_gap_list_only_contains_gaps_that_still_exist(console_routes):
    """v1.22 emptied KNOWN_GAPS_DEFERRED_TO_V1_22. This test now mostly
    runs as a guard rail — if a future PR adds entries here, they had
    better still be missing auth (otherwise the entry is stale)."""
    stale = []
    fixed = []
    actual_routes = {(p, m): _route_has_auth(d, f) for p, m, d, f in console_routes}
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


def test_v22_gap_list_is_empty():
    """v1.22 success criterion: KNOWN_GAPS_DEFERRED_TO_V1_22 must stay
    empty. Adding entries to silence test_no_unexpected_public_endpoint
    defeats the purpose of v1.22 and earns this loud failure."""
    assert KNOWN_GAPS_DEFERRED_TO_V1_22 == set(), (
        f"KNOWN_GAPS_DEFERRED_TO_V1_22 is no longer empty: "
        f"{sorted(KNOWN_GAPS_DEFERRED_TO_V1_22)}. Add the auth dep to "
        f"the route(s) instead of adding entries here."
    )

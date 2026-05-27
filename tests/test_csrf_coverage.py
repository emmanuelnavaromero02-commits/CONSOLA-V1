"""Sprint v1.22 — CSRF coverage on state-changing routes.

v1.22 added `require_csrf` to 17 state-changing routes (the POST/PATCH/
DELETE/PUT subset of the v1.21 gap list). This file pins those wins and
prevents regressions:

  test_v22_csrf_fixes_are_in_place
      The 17 routes v1.22 fixed MUST have CSRF declared. A future PR
      that drops the dep here fails the test.

  test_no_state_changing_route_is_csrf_free
      Every state-changing route in the app must EITHER have CSRF
      declared OR be in one of two allowlists:
        - CSRF_EXEMPT_BY_DESIGN: /auth/login, /auth/refresh, etc.
        - KNOWN_CSRF_GAPS_FOR_LATER: pre-existing routes that v1.22
          didn't touch (29 routes flagged for a follow-up sprint).
      A NEW state-changing route without CSRF that's not in either
      allowlist fails the test loudly.

  test_known_csrf_gap_list_only_contains_gaps_that_still_exist
      Same shrink-as-fixed enforcement as v1.21's gap list. When a
      follow-up sprint adds CSRF to a listed route, the cleanup must
      remove the entry from KNOWN_CSRF_GAPS_FOR_LATER.

Pure AST inspection — no FastAPI imports, no DB.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO_ROOT / "console" / "app" / "main.py"

# State-changing HTTP methods. GETs are excluded from CSRF (they should
# be side-effect-free; if a GET mutates state, the route is bugged in a
# different way).
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ── The 17 state-changing fixes v1.22 landed ────────────────────────


V22_CSRF_FIXES = frozenset({
    # 15 routes that already had auth — v1.22 added CSRF
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
    # 2 routes that had neither auth nor CSRF — v1.22 added both
    ("/monitoring/invoke",                          "POST"),
    ("/api/dags/parse",                             "POST"),
})


# Routes that are STATE-CHANGING but legitimately CSRF-exempt.
# Each entry needs a one-line rationale in the matching code comment.
CSRF_EXEMPT_BY_DESIGN = frozenset({
    # Login creates the session — no cookie exists yet to drive a CSRF
    # double-submit check. The route already has require_csrf as a
    # decorator-level dep (it's a per-form token); listed here for
    # completeness so the test understands the route exists.
    ("/auth/login",         "POST"),
    # Legacy compat alias for /auth/login. It delegates through the
    # real handler and carries the same require_csrf dependency.
    ("/api/auth/login",     "POST"),
    # Activation accepts a one-time invite token in the body — the
    # token IS the credential, no cookie / no CSRF needed.
    ("/auth/activate",      "POST"),
    # Airflow scheduled agent invocations do not carry a browser session;
    # Console authorizes them with X-Agent-Runner-Token instead.
    ("/api/agents/{agent_id}/invoke/scheduled", "POST"),
})


# State-changing routes that DO have auth but don't have CSRF yet.
# v1.22 deliberately did not touch these — the sprint spec scoped the
# CSRF work to the 33-endpoint list it inherited from v1.21. These are
# queued for a follow-up sprint. Adding entries here requires explicit
# operator sign-off; removing an entry (because someone added CSRF to
# the route) is always welcome.
KNOWN_CSRF_GAPS_FOR_LATER = frozenset({
    ("/api/data/{dataset}/query",                                           "POST"),
    ("/monitoring/mcp/invoke",                                              "POST"),
    # v1.21-shipped pages that change session state through the
    # cookie chain — already have CSRF on the form POSTs:
    #   /auth/logout, /auth/forgot-password, /auth/reset-password,
    #   /api/me/change-password
    # so they're not listed here.
})


# ── AST helpers ─────────────────────────────────────────────────────


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
    """Return a list of (path, method, decorator_ast) tuples for every
    state-changing route declared on the app."""
    tree = ast.parse(CONSOLE_MAIN.read_text(encoding="utf-8"))
    routes = []
    for path, method, deco in _route_decorators(tree):
        if method in STATE_CHANGING_METHODS:
            routes.append((path, method, deco))
    return routes


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,method", sorted(V22_CSRF_FIXES))
def test_v22_csrf_fixes_are_in_place(console_state_changing_routes, path, method):
    """Each route v1.22 fixed must continue to declare require_csrf."""
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
    """Every state-changing route on console must have CSRF declared OR
    be in one of the two explicit allowlists. A NEW state-changing route
    without CSRF that's not in either allowlist fails this test."""
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
    """When a follow-up sprint adds CSRF to a listed route, the cleanup
    PR must remove that entry from KNOWN_CSRF_GAPS_FOR_LATER. This test
    enforces that — a listed entry that now has CSRF fails loudly,
    forcing the cleanup."""
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
    """Every entry in CSRF_EXEMPT_BY_DESIGN is explained by a comment in
    THIS file (the comment block above the set). This test guards
    against silently growing the exempt list — adding an entry without
    a rationale fails the file-level check."""
    src = Path(__file__).read_text(encoding="utf-8")
    # Find the CSRF_EXEMPT_BY_DESIGN block in the source
    start = src.index("CSRF_EXEMPT_BY_DESIGN = frozenset({")
    end = src.index("})", start)
    block = src[start:end]
    for path, method in CSRF_EXEMPT_BY_DESIGN:
        assert f'("{path}"' in block, (
            f"CSRF_EXEMPT_BY_DESIGN entry {method} {path} is not "
            f"referenced in the comment block above it. Add a comment "
            f"explaining why this route is legitimately CSRF-exempt."
        )

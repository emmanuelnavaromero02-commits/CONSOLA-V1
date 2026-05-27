"""Workspace CSRF — Double Submit Cookie tests.

Two layers:
  1. The helper in isolation against a small FastAPI mini-app (fast, no
     asyncpg / live Postgres), mirroring console/tests/test_csrf.py.
  2. A coverage check that asserts every state-changing workspace route
     declares the ``require_csrf`` dependency, so a future handler added
     without protection fails the suite.

Locked-down behaviour:
  * Cookie + matching ``X-CSRF-Token`` header → 200.
  * Cookie + matching ``_csrf`` body field   → 200.
  * Missing cookie                            → 403.
  * Cookie but no header / body field         → 403.
  * Mismatching values                        → 403.
  * GET routes are never blocked by CSRF.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# app.main reads INTERNAL_API_KEY at import time via get_internal_api_key();
# the helper rejects keys < 32 chars or with obvious dev-default fragments.
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.services.csrf import (  # noqa: E402
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    require_csrf,
    verify_csrf,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Mini-app fixture ──────────────────────────────────────────────────

def _build_app() -> TestClient:
    app = FastAPI()

    @app.post("/protected", dependencies=[Depends(require_csrf)])
    def protected():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _build_app()


# ── Token generation ──────────────────────────────────────────────────

def test_generate_token_is_unpredictable():
    a = generate_csrf_token()
    b = generate_csrf_token()
    assert a != b
    assert len(a) >= 32  # token_urlsafe(32) yields ~43 chars


# ── verify_csrf primitive ─────────────────────────────────────────────

def test_verify_csrf_requires_cookie_and_provided_match():
    app = FastAPI()

    @app.post("/check")
    def check(request: Request):
        return {"ok": verify_csrf(request)}

    c = TestClient(app)
    # No cookie, no header → False.
    assert c.post("/check").json() == {"ok": False}
    # Cookie + matching header → True.
    c.cookies.set(CSRF_COOKIE_NAME, "abc")
    assert c.post("/check", headers={CSRF_HEADER_NAME: "abc"}).json() == {"ok": True}
    # Cookie + mismatching header → False.
    assert c.post("/check", headers={CSRF_HEADER_NAME: "xyz"}).json() == {"ok": False}


# ── Protected endpoint: positive and negative paths ───────────────────

def test_protected_without_csrf_returns_403(client):
    r = client.post("/protected", json={})
    assert r.status_code == 403
    assert "csrf" in r.json()["detail"].lower()


def test_protected_with_cookie_but_no_header_returns_403(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-1")
    assert client.post("/protected", json={}).status_code == 403


def test_protected_with_mismatching_header_returns_403(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-1")
    r = client.post("/protected", json={}, headers={CSRF_HEADER_NAME: "tok-2"})
    assert r.status_code == 403


def test_protected_with_matching_header_returns_200(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-A")
    r = client.post("/protected", json={}, headers={CSRF_HEADER_NAME: "tok-A"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_protected_with_matching_body_field_returns_200(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-B")
    r = client.post("/protected", json={"_csrf": "tok-B", "extra": 1})
    assert r.status_code == 200


def test_protected_body_field_must_also_match(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-C")
    assert client.post("/protected", json={"_csrf": "WRONG"}).status_code == 403


def test_bearer_auth_is_exempt(client):
    # Bearer-authed requests skip CSRF (no auto-attached cookie to abuse).
    r = client.post("/protected", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 200


# ── Coverage: real workspace routes must declare require_csrf ─────────

def _load_main():
    """Lazy app.main loader; isolate workspace/app from peer service imports."""
    sys.path.insert(0, str(REPO_ROOT / "workspace"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    return importlib.import_module("app.main")


# (METHOD, PATH) pairs that mutate state and MUST be CSRF-protected.
_PROTECTED = {
    ("POST", "/auth/logout"),
    ("POST", "/workspace/chat"),
    ("POST", "/workspace/chat/refresh-context"),
    ("POST", "/workspace/chat/stream"),
    ("POST", "/api/data/{dataset}/query"),
    ("POST", "/api/decisions"),
    ("PATCH", "/api/decisions/{decision_id}"),
    ("DELETE", "/api/decisions/{decision_id}"),
    ("POST", "/api/decisions/{decision_id}/actions"),
}


def test_all_mutating_workspace_routes_require_csrf():
    main = _load_main()
    from app.services.csrf import require_csrf as ws_require_csrf  # this app's copy

    found = {}
    for route in main.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        for method in methods:
            if (method, path) in _PROTECTED:
                has = any(
                    getattr(dep, "call", None) is ws_require_csrf
                    for dep in route.dependant.dependencies
                )
                found[(method, path)] = has

    missing_route = _PROTECTED - set(found)
    assert not missing_route, f"expected protected routes not found: {missing_route}"
    unprotected = {k for k, v in found.items() if not v}
    assert not unprotected, f"mutating routes missing require_csrf: {unprotected}"


# Representative read-only routes that MUST stay CSRF-free so GETs keep
# working (no cookie/header gymnastics for plain reads).
_GET_ROUTES = {
    "/healthz",
    "/auth/me",
    "/api/apps",
    "/api/decisions",
    "/api/data/{dataset}",
}


def test_get_routes_do_not_require_csrf():
    main = _load_main()
    from app.services.csrf import require_csrf as ws_require_csrf

    for route in main.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if path in _GET_ROUTES and "GET" in methods:
            has = any(
                getattr(dep, "call", None) is ws_require_csrf
                for dep in route.dependant.dependencies
            )
            assert not has, f"GET {path} should not require CSRF"

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.services.csrf import (  # noqa: E402
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    require_csrf,
    verify_csrf,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_app() -> TestClient:
    app = FastAPI()

    @app.post("/protected", dependencies=[Depends(require_csrf)])
    def protected():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _build_app()


def test_generate_token_is_unpredictable():
    a = generate_csrf_token()
    b = generate_csrf_token()
    assert a != b
    assert len(a) >= 32


def test_verify_csrf_requires_cookie_and_provided_match():
    app = FastAPI()

    @app.post("/check")
    def check(request: Request):
        return {"ok": verify_csrf(request)}

    c = TestClient(app)
    assert c.post("/check").json() == {"ok": False}
    c.cookies.set(CSRF_COOKIE_NAME, "abc")
    assert c.post("/check", headers={CSRF_HEADER_NAME: "abc"}).json() == {"ok": True}
    assert c.post("/check", headers={CSRF_HEADER_NAME: "xyz"}).json() == {"ok": False}


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
    r = client.post("/protected", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 200


def _load_main():
    sys.path.insert(0, str(REPO_ROOT / "workspace"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    return importlib.import_module("app.main")


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
    from app.services.csrf import require_csrf as ws_require_csrf

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


def test_cors_preflight_allows_csrf_header_before_auth():
    main = _load_main()
    client = TestClient(main.app)
    r = client.options(
        "/api/decisions",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token, content-type",
        },
    )
    assert r.status_code == 200, f"preflight should be answered by CORS, got {r.status_code}"
    allow_headers = (r.headers.get("access-control-allow-headers") or "").lower()
    assert "x-csrf-token" in allow_headers, allow_headers
    allow_methods = (r.headers.get("access-control-allow-methods") or "").upper()
    assert "PATCH" in allow_methods, allow_methods

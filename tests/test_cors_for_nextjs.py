from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))


@pytest.fixture(scope="module")
def client(monkeypatch_module):
    monkeypatch_module.setenv("APP_ENV", "test")
    monkeypatch_module.setenv("INTERNAL_API_KEY", "x" * 64)
    if "FIELD_ENCRYPTION_KEY" not in os.environ:
        monkeypatch_module.setenv(
            "FIELD_ENCRYPTION_KEY",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        )

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


def test_options_preflight_returns_allow_origin(client):
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token,Content-Type",
        },
    )
    assert r.status_code == 200, (
        f"OPTIONS preflight to /auth/login returned {r.status_code} — "
        f"the CORS middleware short-circuit is not firing. Verify "
        f"CORSMiddleware is OUTERMOST in the stack."
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
    allow_headers = r.headers.get("access-control-allow-headers", "")
    assert "X-CSRF-Token".lower() in allow_headers.lower()
    assert "Content-Type".lower() in allow_headers.lower()


def test_options_preflight_caches_for_an_hour(client):
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-max-age") == "3600"


def test_options_preflight_for_disallowed_origin_returns_no_allow_origin(client):
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert (
        r.headers.get("access-control-allow-origin") is None
        or r.headers.get("access-control-allow-origin") != "http://evil.example.com"
    )


def test_get_login_includes_access_control_allow_origin(client):
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001", (
        f"GET /login Origin=:8001 returned headers: {dict(r.headers)} — "
        f"Allow-Origin missing. CORS middleware ordering may be wrong."
    )


def test_post_auth_login_with_origin_returns_allow_origin(client):
    r = client.post(
        "/auth/login",
        json={"email": "nobody@invalid.local", "password": "wrong"},
        headers={
            "Origin": "http://localhost:8001",
            "Content-Type": "application/json",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001", (
        f"POST /auth/login Origin=:8001 missing Allow-Origin (status="
        f"{r.status_code}, headers={dict(r.headers)})"
    )


def test_credentials_true_in_response(client):
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_x_csrf_token_in_allow_headers(client):
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    allow_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allow_headers


def test_decision_action_preflight_allows_idempotency_header(client):
    r = client.options(
        "/api/decisions/900030/actions",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "Content-Type,Idempotency-Key,X-CSRF-Token"
            ),
        },
    )
    assert r.status_code == 200
    allowed = {
        value.strip().lower()
        for value in r.headers.get("access-control-allow-headers", "").split(",")
    }
    assert {"content-type", "idempotency-key", "x-csrf-token"} <= allowed
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001"


def test_expose_headers_includes_set_cookie_and_csrf(client):
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    expose = r.headers.get("access-control-expose-headers", "").lower()
    assert "set-cookie" in expose, (
        "Allow-Expose-Headers must include Set-Cookie — without it the "
        "browser hides the csrf_token + mod_session cookies from JS"
    )
    assert "x-csrf-token" in expose


def test_cors_middleware_is_outermost(client):
    from starlette.middleware.cors import CORSMiddleware
    from app.main import app

    outermost = app.user_middleware[0]
    assert outermost.cls is CORSMiddleware, (
        f"CORSMiddleware must be OUTERMOST (user_middleware[0]). "
        f"Current outermost middleware: {outermost.cls.__name__}. "
        f"Full registration order (outermost → innermost): "
        f"{[m.cls.__name__ for m in app.user_middleware]}"
    )


def test_middleware_stack_full_snapshot(client):
    from app.main import app

    def semantic_name(middleware):
        dispatch = middleware.kwargs.get("dispatch")
        if dispatch is not None:
            return dispatch.__name__
        return middleware.cls.__name__

    actual = [semantic_name(middleware) for middleware in app.user_middleware]
    expected = [
        "CORSMiddleware",
        "RequestIDMiddleware",
        "auth_middleware",
        "security_headers_middleware",
        "api_navigation_guard_middleware",
    ]
    assert actual == expected, (
        f"Middleware ordering changed unexpectedly.\n"
        f"  expected (outermost → innermost): {expected}\n"
        f"  actual:                          {actual}\n"
        f"If this change was intentional, update this test AND verify "
        f"that:\n"
        f"  - CORSMiddleware is still OUTERMOST (Allow-Origin on 401s)\n"
        f"  - RequestIDMiddleware is still SECOND (X-Request-ID on 401s)\n"
        f"  - auth_middleware still wraps the inner stack"
    )


def test_request_id_middleware_remains_second_outermost(client):
    from app.middleware.request_id import RequestIDMiddleware
    from app.main import app

    second = app.user_middleware[1]
    assert second.cls is RequestIDMiddleware, (
        f"RequestIDMiddleware must be SECOND-outermost "
        f"(user_middleware[1]). Found: {second.cls.__name__}. "
        f"Full order (outermost → innermost): "
        f"{[m.cls.__name__ for m in app.user_middleware]}"
    )

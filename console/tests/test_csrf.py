from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.services.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    clear_csrf_cookie,
    generate_csrf_token,
    require_csrf,
    set_csrf_cookie,
    verify_csrf,
)


def _build_app():
    app = FastAPI()

    @app.get("/seed")
    def seed():
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        set_csrf_cookie(resp)
        return resp

    @app.post("/protected", dependencies=[Depends(require_csrf)])
    def protected():
        return {"ok": True}

    @app.post("/protected-rotate", dependencies=[Depends(require_csrf)])
    def protected_rotate():
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        set_csrf_cookie(resp)
        return resp

    @app.post("/protected-clear", dependencies=[Depends(require_csrf)])
    def protected_clear():
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        clear_csrf_cookie(resp)
        return resp

    return TestClient(app)


@pytest.fixture
def client():
    return _build_app()


def test_generate_token_is_unpredictable():
    a = generate_csrf_token()
    b = generate_csrf_token()
    assert a != b
    assert len(a) >= 32


def test_seed_sets_csrf_cookie(client):
    r = client.get("/seed")
    assert r.status_code == 200
    assert CSRF_COOKIE_NAME in r.cookies
    assert r.cookies[CSRF_COOKIE_NAME], "csrf_token cookie should be non-empty"


def test_verify_csrf_requires_cookie_and_provided_match():
    app = FastAPI()

    @app.post("/check-header")
    def check_header(request: __import__("fastapi").Request):
        return {"ok": verify_csrf(request)}

    c = TestClient(app)

    assert c.post("/check-header").json() == {"ok": False}

    c.cookies.set(CSRF_COOKIE_NAME, "abc")
    r = c.post("/check-header", headers={CSRF_HEADER_NAME: "abc"})
    assert r.json() == {"ok": True}

    r = c.post("/check-header", headers={CSRF_HEADER_NAME: "xyz"})
    assert r.json() == {"ok": False}


def test_protected_without_csrf_returns_403(client):
    r = client.post("/protected", json={})
    assert r.status_code == 403
    assert "csrf" in r.json()["detail"].lower()


def test_protected_with_cookie_but_no_header_returns_403(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-1")
    r = client.post("/protected", json={})
    assert r.status_code == 403


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
    r = client.post("/protected", json={"_csrf": "WRONG"})
    assert r.status_code == 403


def test_header_path_short_circuits_body_lookup(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-D")
    r = client.post(
        "/protected",
        json={"_csrf": "tok-D"},
        headers={CSRF_HEADER_NAME: "WRONG"},
    )
    assert r.status_code == 403


def test_rotation_invalidates_previous_token(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-E")
    r1 = client.post("/protected-rotate", headers={CSRF_HEADER_NAME: "tok-E"})
    assert r1.status_code == 200
    new_token = None
    for h in r1.headers.get_list("set-cookie") if hasattr(r1.headers, "get_list") else [r1.headers.get("set-cookie", "")]:
        if h.startswith(f"{CSRF_COOKIE_NAME}="):
            new_token = h.split(";", 1)[0].split("=", 1)[1]
            break
    assert new_token and new_token != "tok-E"

    client.cookies.clear()
    client.cookies.set(CSRF_COOKIE_NAME, new_token)

    r2 = client.post("/protected-rotate", headers={CSRF_HEADER_NAME: "tok-E"})
    assert r2.status_code == 403
    r3 = client.post("/protected-rotate", headers={CSRF_HEADER_NAME: new_token})
    assert r3.status_code == 200


def test_clear_csrf_cookie_drops_the_cookie(client):
    client.cookies.set(CSRF_COOKIE_NAME, "tok-F")
    r = client.post("/protected-clear", headers={CSRF_HEADER_NAME: "tok-F"})
    assert r.status_code == 200
    set_cookie_headers = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [r.headers.get("set-cookie", "")]
    assert any(
        CSRF_COOKIE_NAME in h and ("Max-Age=0" in h or "expires=Thu, 01 Jan 1970" in h.lower() or 'csrf_token=""' in h or 'csrf_token=;' in h)
        for h in set_cookie_headers
    ), f"expected a delete Set-Cookie for {CSRF_COOKIE_NAME}, got: {set_cookie_headers!r}"

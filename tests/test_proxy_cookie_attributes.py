"""Cookie and CSRF guards for the FastAPI-served static console."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MAIN_PY = REPO / "console/app/main.py"
API_TS = REPO / "console-next/src/lib/api.ts"
PAGES_PY = REPO / "console/app/routers/pages.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_next_cookie_proxy_remains():
    assert not (REPO / "console-next/src/lib/proxy.ts").exists()
    assert not (REPO / "console-next/src/proxy.ts").exists()


def test_api_fetch_keeps_browser_cookie_round_trip_native():
    src = _read(API_TS)
    assert 'credentials: init.credentials ?? "include"' in src
    assert "apiFetch" in src
    assert "fetch(path" in src
    assert "getSetCookie" not in src


def test_console_next_pages_seed_csrf_cookie():
    src = _read(PAGES_PY)
    assert "CSRF_COOKIE_NAME" in src
    assert "set_csrf_cookie(response, request.cookies.get(CSRF_COOKIE_NAME))" in src


def test_backend_login_sets_httponly_on_mod_session():
    main_py = _read(MAIN_PY)
    import re

    block = re.search(
        r"resp\.set_cookie\(\s*_auth\.COOKIE_NAME[\s\S]{0,300}?\)",
        main_py,
    )
    assert block, "mod_session set_cookie() call not found in main.py"
    body = block.group(0)
    assert "httponly=True" in body, (
        "console/app/main.py /auth/login must set httponly=True on "
        "the mod_session cookie. Found: " + body[:400]
    )


def test_backend_login_sets_httponly_on_refresh_token():
    main_py = _read(MAIN_PY)
    import re

    block = re.search(
        r"resp\.set_cookie\(\s*_auth\.REFRESH_COOKIE_NAME[\s\S]{0,300}?\)",
        main_py,
    )
    assert block, "refresh_token set_cookie() call not found in main.py"
    body = block.group(0)
    assert "httponly=True" in body, (
        "refresh_token cookie must be HttpOnly. Found: " + body[:400]
    )


def test_backend_logout_deletes_session_cookies():
    main_py = _read(MAIN_PY)
    assert "resp.delete_cookie(_auth.COOKIE_NAME" in main_py
    assert "resp.delete_cookie(_auth.REFRESH_COOKIE_NAME" in main_py
    assert "clear_csrf_cookie(resp)" in main_py

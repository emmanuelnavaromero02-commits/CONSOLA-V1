"""Sprint v1.44.3.3 Task E — proxy must preserve Set-Cookie attributes.

The user's brief reported that ``HttpOnly`` was missing from
session cookies after login. The earlier audit confirmed the
backend SETS HttpOnly correctly
(``console/app/main.py:824-831``); if HttpOnly is missing
downstream, the bug is in how the R-Mac-4 Next.js same-origin
proxy forwards Set-Cookie headers from the backend to the
browser.

This file is a STATIC regression guard against accidentally
re-introducing the bug. It reads ``console-next/src/lib/proxy.ts``
and verifies the cookie-handling code path still:

  - uses ``Headers.getSetCookie()`` to enumerate every Set-Cookie
    header (sonner ``forEach`` collapses them comma-joined which
    breaks both parsing AND attribute preservation),
  - appends each cookie verbatim via ``responseHeaders.append``
    instead of ``set`` (set would overwrite the previous
    Set-Cookie line),
  - never strips ``HttpOnly`` / ``Secure`` / ``SameSite``
    fragments from the value (a defensive forEach loop that
    "cleaned up" cookies would silently undermine the cookie
    flags),
  - does NOT include ``set-cookie`` in the response-header drop
    list (would silently nuke cookies on the way back to the
    browser).

The live HTTP test in ``tests/test_nextjs_proxy.py``
already covers Set-Cookie presence end-to-end when the stack is
up; this file adds source-level safety nets that run on every
commit regardless of docker availability.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROXY_LIB = REPO / "console-next/src/lib/proxy.ts"


def _read() -> str:
    return PROXY_LIB.read_text(encoding="utf-8")


def test_proxy_uses_getSetCookie_for_enumeration():
    """``Headers.forEach`` joins multiple Set-Cookie values with
    ``, `` which is parseable in theory but in practice corrupts
    cookies whose value contains commas (csrf_token base64,
    refresh_token signature). Must use ``getSetCookie()``."""
    src = _read()
    assert "getSetCookie" in src, (
        "lib/proxy.ts must use Headers.getSetCookie() — without it "
        "multiple Set-Cookie headers (csrf_token + mod_session + "
        "refresh_token) get collapsed and the browser drops them."
    )


def test_proxy_appends_set_cookie_does_not_overwrite():
    """``responseHeaders.set('set-cookie', ...)`` overwrites the
    previous Set-Cookie line; only one cookie survives. The proxy
    must ``append`` instead."""
    src = _read()
    assert 'responseHeaders.append("set-cookie"' in src, (
        "lib/proxy.ts must call responseHeaders.append('set-cookie', "
        "value) — `.set()` would overwrite earlier Set-Cookie "
        "headers and only the last cookie would reach the browser."
    )


def test_proxy_does_not_drop_set_cookie_in_RES_DROP():
    """If a future refactor adds ``set-cookie`` to the drop list,
    every cookie response disappears silently. Make the drop list
    explicit and audit-friendly."""
    src = _read()
    # Find the RES_DROP literal and verify set-cookie isn't in it.
    import re
    m = re.search(r"const RES_DROP = new Set\(\[([\s\S]*?)\]\)", src)
    assert m, "RES_DROP set definition not found in proxy.ts"
    body = m.group(1).lower()
    assert "set-cookie" not in body, (
        "lib/proxy.ts RES_DROP must NOT include 'set-cookie' — that "
        "would strip every cookie from upstream responses and "
        "silently break login."
    )


def test_proxy_does_not_mutate_cookie_values():
    """Defensive: ensure the cookie loop appends the VERBATIM
    upstream value. A future refactor that maps/filters the
    value would silently strip HttpOnly / Secure / SameSite
    attributes."""
    src = _read()
    import re
    # The append must come from the iteration variable directly.
    pattern = re.compile(
        r"for \(const cookie of getSetCookie[\s\S]{0,200}?"
        r"responseHeaders\.append\(\"set-cookie\",\s*cookie\)",
    )
    assert pattern.search(src), (
        "lib/proxy.ts must append the upstream cookie value VERBATIM "
        "(``responseHeaders.append('set-cookie', cookie)``). A "
        "map/filter step would risk stripping HttpOnly / Secure / "
        "SameSite attributes."
    )


def test_backend_login_sets_httponly_on_mod_session():
    """Source-level pin: the backend must continue to set
    ``httponly=True`` on the mod_session cookie. If a future
    refactor flips it false, the proxy's perfect forwarding
    becomes irrelevant — the cookie ships without HttpOnly."""
    main_py = (REPO / "console/app/main.py").read_text(encoding="utf-8")
    # Locate the mod_session set_cookie call window.
    import re
    block = re.search(
        r"resp\.set_cookie\(\s*_auth\.COOKIE_NAME[\s\S]{0,300}?\)",
        main_py,
    )
    assert block, "mod_session set_cookie() call not found in main.py"
    body = block.group(0)
    assert "httponly=True" in body, (
        "console/app/main.py /auth/login must set ``httponly=True`` on "
        "the mod_session cookie. Found: " + body[:400]
    )


def test_backend_login_sets_httponly_on_refresh_token():
    """Same pin for refresh_token — set via _set_refresh_cookie."""
    main_py = (REPO / "console/app/main.py").read_text(encoding="utf-8")
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
    """The user's brief reported logout not clearing cookies.
    The audit found the backend DOES delete them. Pin both
    delete_cookie calls so a future refactor can't silently
    drop them and re-introduce the bug."""
    main_py = (REPO / "console/app/main.py").read_text(encoding="utf-8")
    # Look for both delete_cookie calls inside ANY auth/logout
    # handler (location-agnostic — the audit found them at
    # main.py:888-890 but a refactor could move them).
    assert "resp.delete_cookie(_auth.COOKIE_NAME" in main_py
    assert "resp.delete_cookie(_auth.REFRESH_COOKIE_NAME" in main_py


def test_next_proxy_recognizes_mod_session():
    """R-Mac-4 added mod_session + refresh_token to the
    AUTH_COOKIE_CANDIDATES list. Without it the Next proxy
    silently redirects authenticated users back to /login."""
    proxy = (REPO / "console-next/src/proxy.ts").read_text(encoding="utf-8")
    assert '"mod_session"' in proxy, (
        "console-next/src/proxy.ts must include mod_session in "
        "AUTH_COOKIE_CANDIDATES — without it authenticated users "
        "loop back to /login because the cookie isn't recognised."
    )
    assert '"refresh_token"' in proxy

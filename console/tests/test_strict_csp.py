from __future__ import annotations

import re
from pathlib import Path

import importlib
import sys
from fastapi.responses import JSONResponse


import os
from unittest.mock import MagicMock

os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)
os.environ.setdefault("JWT_SECRET_KEY",   "y" * 64)
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("MINIO_SECRET_KEY",  "test")


def _main():
    for _k in ("app.dependencies", "app.services.auth"):
        if isinstance(sys.modules.get(_k), MagicMock):
            sys.modules.pop(_k, None)
    return importlib.import_module("app.main")


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "console" / "app" / "static"


def _csp_for(path: str) -> str:
    main_module = _main()
    resp = JSONResponse({})
    main_module._apply_security_headers(resp, path)
    return resp.headers.get("content-security-policy", "")


def test_login_returns_strict_csp_without_unsafe_inline_script():
    csp = _csp_for("/login")
    assert "script-src 'self';" in csp or "script-src 'self' " in csp, csp
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0], (
        "script-src still allows unsafe-inline: " + csp
    )


def test_me_returns_strict_csp():
    assert "'unsafe-inline'" not in _csp_for("/me").split("style-src", 1)[0]


def test_forgot_password_returns_strict_csp():
    assert "'unsafe-inline'" not in _csp_for("/forgot-password").split("style-src", 1)[0]


def test_reset_password_returns_strict_csp():
    assert "'unsafe-inline'" not in _csp_for("/reset-password").split("style-src", 1)[0]


def test_activate_returns_strict_csp():
    assert "'unsafe-inline'" not in _csp_for("/activate").split("style-src", 1)[0]


def test_home_uses_strict_csp_phase3():
    csp = _csp_for("/")
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0], csp


def test_studio_uses_strict_csp_phase3():
    csp = _csp_for("/studio")
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0], csp


def test_strict_csp_blocks_frame_embedding():
    csp = _csp_for("/login")
    assert "frame-ancestors 'none'" in csp


def test_strict_csp_constrains_form_action():
    csp = _csp_for("/login")
    assert "form-action 'self'" in csp


def test_strict_csp_paths_set_matches_expected():
    expected = {"/login", "/me", "/forgot-password", "/reset-password", "/activate"}
    assert set(_main()._STRICT_CSP_PATHS) == expected


_FORM_PAGES = [
    "login.html",
    "me.html",
    "forgot_password.html",
    "reset_password.html",
    "activate.html",
]
_EXTRACTED_JS = {
    "login.html": "login.js",
    "me.html": "me.js",
    "forgot_password.html": "forgot_password.js",
    "reset_password.html": "reset_password.js",
    "activate.html": "activate.js",
}
_INLINE_HANDLER_RE = re.compile(r'\bon(?:click|submit|change|input|keydown|mousedown|load|error)\s*=\s*"', re.IGNORECASE)


def test_auth_html_pages_have_no_inline_event_handlers():
    for page in _FORM_PAGES:
        html = (STATIC / page).read_text(encoding="utf-8")
        assert not _INLINE_HANDLER_RE.search(html), (
            f"{page} still has an inline on*= handler — strict CSP will block it.\n"
            f"matches: {_INLINE_HANDLER_RE.findall(html)!r}"
        )


def test_auth_html_pages_have_no_inline_script_blocks_with_code():
    bad_inline = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>([^<]+?)</script>", re.IGNORECASE | re.DOTALL)
    for page in _FORM_PAGES:
        html = (STATIC / page).read_text(encoding="utf-8")
        matches = [m.strip() for m in bad_inline.findall(html) if m.strip()]
        assert not matches, (
            f"{page} still has an inline <script> with code — strict CSP will block it.\n"
            f"first 200 chars: {matches[0][:200]!r}"
        )


def test_each_auth_html_references_its_extracted_js():
    for page, js_name in _EXTRACTED_JS.items():
        html = (STATIC / page).read_text(encoding="utf-8")
        expected = f'/static/js/{js_name}'
        assert expected in html, (
            f"{page} should reference {expected} (no inline script remained)."
        )
        js_file = STATIC / "js" / js_name
        assert js_file.is_file(), f"missing extracted file {js_file}"
        js_src = js_file.read_text(encoding="utf-8")
        assert "addEventListener" in js_src, (
            f"{js_name} should use addEventListener (the inline onsubmit was removed)."
        )

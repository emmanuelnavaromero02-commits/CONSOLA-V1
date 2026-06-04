from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO / "console/app/main.py"
SECURITY = REPO / "SECURITY.md"


def test_console_has_no_dead_app_embed_csp():
    source = CONSOLE_MAIN.read_text(encoding="utf-8")
    assert "APP_EMBED_CSP" not in source


def test_console_csp_does_not_allow_inline_scripts():
    source = CONSOLE_MAIN.read_text(encoding="utf-8")
    assert "script-src 'self';" in source
    assert "script-src 'self' 'unsafe-inline'" not in source
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" not in source


def test_style_inline_exception_is_documented():
    security = SECURITY.read_text(encoding="utf-8")
    assert "CSP inline-style exception" in security
    assert "style-src 'unsafe-inline'" in security
    assert "does not permit inline event handlers or inline `<script>`" in security

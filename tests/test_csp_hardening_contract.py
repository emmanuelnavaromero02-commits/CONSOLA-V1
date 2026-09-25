from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = REPO / "console/app/main.py"
SECURITY_HEADERS = REPO / "console/app/services/security_headers.py"
APP_EMBED = REPO / "console/app/domains/apps/embed.py"


def test_console_has_no_dead_app_embed_csp():
    source = CONSOLE_MAIN.read_text(encoding="utf-8")
    assert "APP_EMBED_CSP" not in source


def test_console_csp_does_not_allow_inline_scripts():
    source = SECURITY_HEADERS.read_text(encoding="utf-8")
    assert "script-src 'self';" in source
    assert "script-src 'self' 'unsafe-inline'" not in source
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" not in source


def test_app_embed_csp_uses_nonce_for_inline_bridge():
    source = APP_EMBED.read_text(encoding="utf-8")
    assert "script-src 'self' 'nonce-{nonce}'" in source
    assert "<script nonce=" in source
    assert "script-src 'self' 'unsafe-inline'" not in source

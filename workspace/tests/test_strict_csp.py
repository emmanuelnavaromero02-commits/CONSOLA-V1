from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse


os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)


def _main():
    sys.path.insert(0, str(REPO_ROOT / "workspace"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    return importlib.import_module("app.main")


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_STATIC = REPO_ROOT / "workspace" / "app" / "static"


def _csp_for(path: str) -> str:
    main_module = _main()
    resp = JSONResponse({})
    main_module._apply_security_headers(resp, path)
    return resp.headers.get("content-security-policy", "")


def test_workspace_csp_no_unsafe_inline_script_on_shell_paths():
    for path in ("/", "/decisions", "/api/decisions", "/healthz", "/auth/me"):
        csp = _csp_for(path)
        script_seg = csp.split("style-src", 1)[0]
        assert "'unsafe-inline'" not in script_seg, (
            f"{path}: script-src still allows 'unsafe-inline' — {script_seg!r}"
        )
        assert "script-src 'self'" in script_seg, (
            f"{path}: missing script-src 'self' — {script_seg!r}"
        )


def test_workspace_csp_keeps_style_unsafe_inline():
    csp = _csp_for("/")
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


def test_workspace_apps_wrapper_allows_only_same_origin_bridge():
    csp = _csp_for("/apps/pnl_ejecutivo")
    script_seg = csp.split("style-src", 1)[0]
    assert "'unsafe-inline'" not in script_seg
    assert "frame-src 'self'" in csp, csp
    assert "cdn.jsdelivr.net" not in csp and "cdnjs.cloudflare.com" not in csp, csp


def test_workspace_apps_content_is_sandboxed_and_cannot_connect():
    csp = _csp_for("/apps/pnl_ejecutivo/content")
    assert "sandbox allow-scripts" in csp, csp
    assert "allow-same-origin" not in csp, csp
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0]
    assert "connect-src 'none'" in csp, csp
    assert "frame-ancestors 'self'" in csp, csp
    assert "cdn.jsdelivr.net" in csp or "cdnjs.cloudflare.com" in csp, csp


def test_workspace_apps_nonce_inline_scripts():
    main_module = _main()
    nonce = "testnonce123"
    wrapper_html = main_module._app_wrapper_html("demo", ["gold_demo"], nonce)
    content_html = main_module._inject_app_bridge("<html><head></head><body><script>run()</script></body></html>", nonce)

    assert f"'nonce-{nonce}'" in main_module._apps_wrapper_csp(nonce)
    assert f"'nonce-{nonce}'" in main_module._apps_content_csp(nonce)
    assert f'<script nonce="{nonce}">' in wrapper_html
    assert f'<script nonce="{nonce}">' in content_html
    assert content_html.count(f'nonce="{nonce}"') == 2


def test_workspace_csp_keeps_frame_ancestors_none():
    csp = _csp_for("/")
    assert "frame-ancestors 'none'" in csp, csp


_INLINE_HANDLER_RE = re.compile(
    r'\bon(?:click|change|submit|input|keydown|mousedown|load|error|'
    r'scroll|mouseover|mouseout|mouseenter|mouseleave|focus|blur)\s*=\s*"',
    re.IGNORECASE,
)
_INLINE_SCRIPT_WITH_CODE_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>([^<]+?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def test_workspace_html_has_no_inline_event_handlers():
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    matches = _INLINE_HANDLER_RE.findall(html)
    assert not matches, (
        f"workspace.html still has inline on*= handlers: {matches!r}"
    )


def test_workspace_html_has_no_inline_script_with_code():
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    bodies = [b.strip() for b in _INLINE_SCRIPT_WITH_CODE_RE.findall(html) if b.strip()]
    assert not bodies, (
        f"workspace.html still has inline <script> with code "
        f"(first 200 chars): {bodies[0][:200]!r}"
    )


def test_workspace_html_references_extracted_js():
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    assert '/static/js/workspace.js' in html, (
        "workspace.html must reference /static/js/workspace.js (the "
        "extracted shell script)"
    )


def test_extracted_workspace_js_exists_and_has_event_listener():
    js_path = WORKSPACE_STATIC / "js" / "workspace.js"
    assert js_path.is_file(), f"missing {js_path}"
    src = js_path.read_text(encoding="utf-8")
    assert "addEventListener" in src, (
        "workspace.js should call addEventListener — the inline on* "
        "handlers were removed and need to bind via the delegated listener"
    )
    expected_actions = (
        "apps-reload", "chat-reset", "chat-example", "chat-send",
        "decision-new", "decision-submit", "modal-close", "modal-backdrop",
        "logout", "kpi-add", "switch-tab", "dec-filter", "detail-tab",
        "decision-action-add", "decision-delete", "decision-save-overview",
        "decision-save-kpis", "kpi-remove",
    )
    missing = [a for a in expected_actions if f"'{a}'" not in src]
    assert not missing, (
        f"workspace.js delegated handler is missing case(s): {missing}"
    )

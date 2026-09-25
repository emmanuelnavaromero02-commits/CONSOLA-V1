from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.responses import JSONResponse

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


_INLINE_HANDLER_RE = re.compile(
    r'\bon(?:click|change|submit|input|keydown|mousedown|load|error|scroll|mouseover|mouseout|mouseenter|mouseleave)\s*=\s*"',
    re.IGNORECASE,
)
_INLINE_SCRIPT_WITH_CODE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>([^<]+?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def test_no_inline_handlers_in_any_root_html():
    for html_path in sorted(STATIC.glob("*.html")):
        html = html_path.read_text(encoding="utf-8")
        matches = _INLINE_HANDLER_RE.findall(html)
        assert not matches, (
            f"{html_path.name} has inline on*= handlers: {matches!r}"
        )


def test_no_inline_script_with_code_in_any_root_html():
    for html_path in sorted(STATIC.glob("*.html")):
        html = html_path.read_text(encoding="utf-8")
        non_whitespace = [m.strip() for m in _INLINE_SCRIPT_WITH_CODE.findall(html) if m.strip()]
        assert not non_whitespace, (
            f"{html_path.name} still has an inline <script> with code "
            f"(first 200 chars): {non_whitespace[0][:200]!r}"
        )


GLOBAL_STRICT_PATHS = [
    "/",
    "/decisions",
    "/monitor",
    "/iam",
    "/studio",
    "/security",
    "/apps-gallery",
    "/rag",
    "/admin/users",
    "/settings",
    "/operations",
]


def test_every_non_viewer_path_has_strict_csp():
    for path in GLOBAL_STRICT_PATHS:
        csp = _csp_for(path)
        script_seg = csp.split("style-src", 1)[0]
        assert "'unsafe-inline'" not in script_seg, (
            f"{path}: script-src still allows 'unsafe-inline' — {script_seg!r}"
        )
        assert "script-src 'self'" in script_seg, (
            f"{path}: missing script-src 'self' — {script_seg!r}"
        )


def test_global_csp_keeps_style_unsafe_inline():
    csp = _csp_for("/")
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


def test_global_csp_keeps_frame_ancestors_none_for_non_viewers():
    csp = _csp_for("/")
    assert "frame-ancestors 'none'" in csp, csp


def test_viewer_path_still_keeps_frame_ancestors_self():
    csp = _csp_for("/viewer/pipeline")
    assert "frame-ancestors 'self'" in csp, csp


def test_app_embed_path_is_frameable_but_script_strict():
    main_module = _main()
    resp = JSONResponse({})
    main_module._apply_security_headers(resp, "/apps/sap_successfactors_talent_health/embed")
    csp = resp.headers.get("content-security-policy", "")
    script_seg = csp.split("style-src", 1)[0]

    assert "frame-ancestors 'self'" in csp, csp
    assert "frame-src 'self'" in csp, csp
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "script-src 'self'" in script_seg, csp
    assert "'unsafe-inline'" not in script_seg, csp


def test_decisions_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/decisions").split("style-src", 1)[0]


def test_monitor_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/monitor").split("style-src", 1)[0]


def test_iam_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/iam").split("style-src", 1)[0]


def test_security_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/security").split("style-src", 1)[0]


def test_apps_gallery_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/apps-gallery").split("style-src", 1)[0]


def test_rag_csp_is_strict():
    assert "'unsafe-inline'" not in _csp_for("/rag").split("style-src", 1)[0]


def test_control_room_fallback_csp_is_strict_for_scripts():
    csp = _csp_for("/control-room")
    script_seg = csp.split("style-src", 1)[0]
    assert "script-src 'self'" in script_seg, csp
    assert "'unsafe-inline'" not in script_seg, csp
    assert "frame-ancestors 'none'" in csp, csp


def test_control_room_talent_fallback_csp_is_strict_for_scripts():
    csp = _csp_for("/control-room/talent")
    script_seg = csp.split("style-src", 1)[0]
    assert "script-src 'self'" in script_seg, csp
    assert "'unsafe-inline'" not in script_seg, csp
    assert "frame-ancestors 'none'" in csp, csp

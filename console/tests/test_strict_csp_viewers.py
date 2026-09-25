from __future__ import annotations

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
    import importlib
    return importlib.import_module("app.main")


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "console" / "app" / "static"
VIEWERS = STATIC / "viewers"
JS_DIR = STATIC / "js" / "viewers"


def _csp_for(path: str) -> str:
    main_module = _main()
    resp = JSONResponse({})
    main_module._apply_security_headers(resp, path)
    return resp.headers.get("content-security-policy", "")


VIEWER_PATHS = [
    "/viewer/pipeline",
    "/viewer/datasets",
    "/viewer/datasets/some_name",
    "/viewer/semantic",
    "/viewer/jobs",
    "/viewer/jobs/job-id-1",
    "/viewer/schema",
    "/viewer/dataset",
    "/viewer/vault",
]


def test_every_viewer_path_drops_unsafe_inline_from_script_src():
    for path in VIEWER_PATHS:
        csp = _csp_for(path)
        script_seg = csp.split("style-src", 1)[0]
        assert "'unsafe-inline'" not in script_seg, (
            f"{path}: script-src still has 'unsafe-inline': {script_seg!r}"
        )
        assert "script-src 'self'" in script_seg, f"{path}: missing script-src 'self' — {script_seg!r}"


def test_viewer_csp_keeps_style_unsafe_inline():
    csp = _csp_for("/viewer/pipeline")
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


def test_viewer_csp_allows_same_origin_iframe():
    csp = _csp_for("/viewer/pipeline")
    assert "frame-ancestors 'self'" in csp, csp


def test_non_viewer_path_also_strict_after_phase3():
    csp = _csp_for("/")
    assert "'unsafe-inline'" not in csp.split("style-src", 1)[0], csp


REFACTORED_PAGES = {
    "pipeline.html":  "pipeline.js",
    "datasets.html":  "datasets.js",
    "dataset.html":   "dataset.js",
    "jobs.html":      "jobs.js",
    "job.html":       "job.js",
    "schema.html":    "schema.js",
    "semantic.html":  "semantic.js",
}

_INLINE_HANDLER_RE = re.compile(
    r'\bon(?:click|change|submit|input|keydown|mousedown|load|error|mouseover|mouseout|mouseenter|mouseleave|scroll)\s*=\s*"',
    re.IGNORECASE,
)
_INLINE_SCRIPT_WITH_CODE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>([^<]+?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def test_every_refactored_viewer_has_no_inline_event_handlers():
    for page in REFACTORED_PAGES:
        html = (VIEWERS / page).read_text(encoding="utf-8")
        matches = _INLINE_HANDLER_RE.findall(html)
        assert not matches, (
            f"{page} still has inline on*= handlers: {matches!r}"
        )


def test_every_refactored_viewer_has_no_inline_script_with_code():
    for page in REFACTORED_PAGES:
        html = (VIEWERS / page).read_text(encoding="utf-8")
        non_whitespace = [m.strip() for m in _INLINE_SCRIPT_WITH_CODE.findall(html) if m.strip()]
        assert not non_whitespace, (
            f"{page} still has an inline <script> with code (first 200 chars): "
            f"{non_whitespace[0][:200]!r}"
        )


def test_every_refactored_viewer_references_its_external_js():
    for page, js_name in REFACTORED_PAGES.items():
        html = (VIEWERS / page).read_text(encoding="utf-8")
        expected = f"/static/js/viewers/{js_name}"
        assert expected in html, (
            f"{page} should reference {expected} now that JS was extracted."
        )


def test_every_extracted_js_file_exists_and_wires_listeners():
    for page, js_name in REFACTORED_PAGES.items():
        js_path = JS_DIR / js_name
        assert js_path.is_file(), f"missing extracted file {js_path}"
        src = js_path.read_text(encoding="utf-8")
        assert "addEventListener" in src, (
            f"{js_name} should call addEventListener (the inline handlers were removed)."
        )


def test_pipeline_js_uses_event_delegation_for_dynamic_handlers():
    src = (JS_DIR / "pipeline.js").read_text(encoding="utf-8")
    for action in ("extract-entity", "select-dag", "apply-template"):
        assert f'data-action="{action}"' in src, (
            f"pipeline.js no longer emits data-action={action!r}"
        )
        assert f"'{action}'" in src, (
            f"pipeline.js delegation no longer handles {action!r}"
        )
    for fn in ("extractEntity", "selectDag", "applyTemplate"):
        assert f"function {fn}" in src or f"async function {fn}" in src


def test_pipeline_extract_all_uses_canonical_endpoint_and_csrf():
    src = (JS_DIR / "pipeline.js").read_text(encoding="utf-8")
    html = (VIEWERS / "pipeline.html").read_text(encoding="utf-8")

    assert 'data-action="extract-all"' in html
    assert "/api/pipeline/${encodeURIComponent(_cartridge)}/extract_all" in src
    assert "X-CSRF-Token" in src
    assert "jsonHeaders()" in src


def test_schema_viewer_preserves_viewer_type_in_history_url():
    src = (JS_DIR / "schema.js").read_text(encoding="utf-8")

    assert "nextParams.set('type', 'schema')" in src
    assert "`?source=${encodeURIComponent(source)}`" not in src


def test_schema_viewer_renders_safe_error_states():
    src = (JS_DIR / "schema.js").read_text(encoding="utf-8")

    assert "function schemaMessage" in src
    assert "function normalizeColumns" in src
    assert "function normalizeRows" in src
    assert "No se pudo cargar el schema." in src
    assert "Sin columnas inferidas" in src


def test_semantic_and_pipeline_default_to_active_scoped_cartridge():
    semantic = (JS_DIR / "semantic.js").read_text(encoding="utf-8")
    pipeline = (JS_DIR / "pipeline.js").read_text(encoding="utf-8")

    for src in (semantic, pipeline):
        assert "active_scoped_cartridges" in src
        assert "api/apps" in src

    assert "|| 'replicon'" not in semantic
    assert "_cartridge    = 'replicon'" not in pipeline

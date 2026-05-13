"""Sprint v1.11 phase 2 — strict CSP on /viewer/* pages.

Mirrors the phase-1 test but for the viewer surface: the seven HTMLs
(pipeline, datasets, dataset, jobs, job, schema, semantic) now have their
JS extracted into /static/js/viewers/*.js, and VIEWER_SECURITY_HEADERS
dropped 'unsafe-inline' from script-src.

vault.html was already clean (no inline JS) and stays in the test set so
its CSP is also covered.

Lazy-imports app.main so this test module's collection doesn't pollute
sys.modules for peer tests.
"""
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
    """Lazy app.main loader; pops stale MagicMock stubs from peer tests."""
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


# ── CSP per viewer path ──────────────────────────────────────────────

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
        # We only check the script-src portion, not style-src.
        script_seg = csp.split("style-src", 1)[0]
        assert "'unsafe-inline'" not in script_seg, (
            f"{path}: script-src still has 'unsafe-inline': {script_seg!r}"
        )
        # And script-src 'self' must be present in some form.
        assert "script-src 'self'" in script_seg, f"{path}: missing script-src 'self' — {script_seg!r}"


def test_viewer_csp_keeps_style_unsafe_inline():
    # The page <style> blocks are still inline and not in scope for this phase.
    csp = _csp_for("/viewer/pipeline")
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


def test_viewer_csp_allows_same_origin_iframe():
    # Viewers are embedded inside Monitor/Studio iframes; frame-ancestors
    # 'self' must stay.
    csp = _csp_for("/viewer/pipeline")
    assert "frame-ancestors 'self'" in csp, csp


def test_non_viewer_path_keeps_relaxed_csp():
    csp = _csp_for("/")
    # The relaxed CSP at the site root still allows inline scripts (Phase 3 work).
    assert "script-src 'self' 'unsafe-inline'" in csp, csp


# ── HTML wiring per viewer ───────────────────────────────────────────

# Five files we just refactored + vault (was already clean) + dataset detail.
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


# ── pipeline.html — dynamic delegation hooks ─────────────────────────

def test_pipeline_js_uses_event_delegation_for_dynamic_handlers():
    """pipeline.js dispatches the three dynamic actions (extract-entity,
    select-dag, apply-template) via data-action delegation. Verify both
    the producer (innerHTML emits the attributes) and the consumer (the
    listener reads them) are present in the file."""
    src = (JS_DIR / "pipeline.js").read_text(encoding="utf-8")
    for action in ("extract-entity", "select-dag", "apply-template"):
        assert f'data-action="{action}"' in src, (
            f"pipeline.js no longer emits data-action={action!r}"
        )
        assert f"'{action}'" in src, (
            f"pipeline.js delegation no longer handles {action!r}"
        )
    # All three callable targets must still be defined.
    for fn in ("extractEntity", "selectDag", "applyTemplate"):
        assert f"function {fn}" in src or f"async function {fn}" in src

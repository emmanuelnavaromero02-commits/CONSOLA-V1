from __future__ import annotations

import os
import sys
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

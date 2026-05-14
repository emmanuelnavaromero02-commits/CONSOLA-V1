"""Sprint v1.31 — monitoring discovery and RAG page must be auth-gated."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _load_console_main():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    os.environ["INTERNAL_API_KEY"] = "test-internal-key-aaaaaaaaaaaaaaaaaaaaaaaa"
    os.environ["JWT_SECRET_KEY"] = "test-jwt-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    return importlib.import_module("app.main")


def test_monitoring_mcp_tools_requires_auth():
    main = _load_console_main()
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.get("/monitoring/mcp/tools")

    assert resp.status_code == 401


def test_monitoring_tools_requires_auth():
    main = _load_console_main()
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.get("/monitoring/tools")

    assert resp.status_code == 401


def test_rag_requires_admin_auth():
    main = _load_console_main()
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.get("/rag", follow_redirects=False)

    assert resp.status_code in (307, 401)

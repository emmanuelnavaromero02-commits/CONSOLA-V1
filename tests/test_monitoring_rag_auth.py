from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import Headers


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
    os.environ["INTERNAL_API_KEY_CONSOLE_TO_CONSOLE"] = os.environ["INTERNAL_API_KEY"]
    os.environ["JWT_SECRET_KEY"] = "test-jwt-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    return importlib.import_module("app.main")


def test_monitoring_mcp_tools_requires_auth():
    main = _load_console_main()
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.get("/monitoring/mcp/tools")

    assert resp.status_code == 401


def test_monitoring_mcp_tools_allows_internal_console_header():
    main = _load_console_main()
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.get(
        "/monitoring/mcp/tools",
        headers={
            "x-api-key": os.environ["INTERNAL_API_KEY"],
            "x-internal-service": "console",
        },
    )

    assert resp.status_code == 200
    assert "tools" in resp.json()


def test_internal_csrf_bypass_is_scoped_to_mcp_paths():
    _load_console_main()
    from app.services import csrf

    headers = Headers({
        "x-api-key": os.environ["INTERNAL_API_KEY"],
        "x-internal-service": "console",
    })
    allowed = SimpleNamespace(
        url=SimpleNamespace(path="/studio_ops/mcp/invoke"),
        headers=headers,
    )
    disallowed = SimpleNamespace(
        url=SimpleNamespace(path="/api/admin/users"),
        headers=headers,
    )

    assert csrf._valid_internal_service_request(allowed)
    assert not csrf._valid_internal_service_request(disallowed)


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

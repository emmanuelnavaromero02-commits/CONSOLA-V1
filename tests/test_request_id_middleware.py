"""Sprint v1.41.1 — Request correlation ID propagation.

Pure-unit tests for the RequestIDMiddleware. We exercise the module
in isolation against a minimal FastAPI app: hitting the real console
would require its full env (DB, vault, JWT keys, etc.) which is
overkill for what this middleware does. The middleware code is
identical across the 5 services (console, workspace, vault, refinement,
mcp-infra), so testing one copy is enough.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def app_with_middleware():
    repo_root = Path(__file__).resolve().parents[1]
    # Drop sibling services so console's app/ wins as the importable package.
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo_root / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from app.middleware.request_id import RequestIDMiddleware, request_id_var

    api = FastAPI()
    api.add_middleware(RequestIDMiddleware)

    @api.get("/echo")
    def echo():
        # Read the contextvar from inside the handler — the middleware
        # must have set it before call_next dispatched.
        return {"rid": request_id_var.get()}

    return api


def test_response_includes_x_request_id_header(app_with_middleware):
    client = TestClient(app_with_middleware)
    r = client.get("/echo")
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid, "X-Request-ID header must be set on every response"
    # Generated rid is a UUID4 — 36 chars including 4 dashes.
    assert len(rid) == 36 and rid.count("-") == 4


def test_client_provided_request_id_is_preserved(app_with_middleware):
    client = TestClient(app_with_middleware)
    incoming = "trace-abc-123"
    r = client.get("/echo", headers={"X-Request-ID": incoming})
    assert r.status_code == 200
    # The middleware must reuse what the client sent (correlation across
    # services) instead of generating a fresh UUID.
    assert r.headers.get("x-request-id") == incoming
    assert r.json()["rid"] == incoming


def test_request_id_appears_in_structured_logs(app_with_middleware):
    """The JSONFormatter pulls request_id from the contextvar fallback
    when no explicit extra={"request_id": ...} is passed."""
    from app.logging_config import JSONFormatter

    formatter = JSONFormatter(service_name="test")

    from app.middleware.request_id import request_id_var

    token = request_id_var.set("rid-from-middleware-xyz")
    try:
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__,
            lineno=0, msg="hello", args=(), exc_info=None,
        )
        out = formatter.format(rec)
    finally:
        request_id_var.reset(token)

    payload = json.loads(out)
    assert payload.get("request_id") == "rid-from-middleware-xyz"


def test_request_id_is_unique_per_request(app_with_middleware):
    client = TestClient(app_with_middleware)
    rids = {client.get("/echo").headers.get("x-request-id") for _ in range(5)}
    assert len(rids) == 5, "every request should generate a fresh UUID"


def test_middleware_module_present_in_all_services():
    """All services ship the identical
    middleware module (byte-equal). v1.43.1 extended the original 5
    to cover the cartridges (Codex P0-1)."""
    import hashlib
    repo = Path(__file__).resolve().parents[1]
    digests = {}
    paths = []
    for svc in ("console", "workspace", "vault", "refinement", "mcp-infra"):
        paths.append((svc, repo / svc / "app" / "middleware" / "request_id.py"))
    for cart in ("replicon", "hubspot", "banxico", "inegi", "sec_edgar", "sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
        paths.append((f"cartridges/{cart}", repo / "cartridges" / cart / "app" / "middleware" / "request_id.py"))
    for svc, path in paths:
        assert path.exists(), f"{svc} missing middleware/request_id.py"
        digests[svc] = hashlib.md5(path.read_bytes()).hexdigest()
    distinct = set(digests.values())
    assert len(distinct) == 1, (
        f"middleware/request_id.py drifted across services: {digests!r}"
    )


def test_cartridge_main_modules_register_request_id_middleware():
    """v1.43.1 (Codex P0-1): every cartridge main.py must register
    the middleware via app.add_middleware. Without this the
    cartridges' 401/403 responses never go through the send-wrapper."""
    repo = Path(__file__).resolve().parents[1]
    for cart in ("replicon", "hubspot", "banxico", "inegi", "sec_edgar", "sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
        src = (repo / "cartridges" / cart / "app" / "main.py").read_text(encoding="utf-8")
        assert "from app.middleware.request_id import RequestIDMiddleware" in src, (
            f"{cart} main.py does not import RequestIDMiddleware"
        )
        assert "app.add_middleware(RequestIDMiddleware)" in src, (
            f"{cart} main.py does not register RequestIDMiddleware"
        )


def test_exception_response_still_carries_request_id(app_with_middleware):
    """Sprint v1.41.1 hardening (observability R1 finding): if the
    downstream handler raises, the middleware must still emit a 500
    response with the X-Request-ID header. Without this guard the
    earlier draft hit UnboundLocalError on `response` after the
    try/finally, losing the correlation header precisely when the
    operator needs it most."""
    from app.middleware.request_id import RequestIDMiddleware

    api = FastAPI()
    api.add_middleware(RequestIDMiddleware)

    @api.get("/boom")
    def boom():
        raise RuntimeError("downstream blew up")

    # TestClient raises by default when the app raises; tell it to
    # surface the response instead.
    client = TestClient(api, raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    rid = r.headers.get("x-request-id")
    assert rid, "X-Request-ID must be present even on 5xx"
    assert len(rid) == 36, "should be a UUID4 when no client header was sent"


def test_exception_response_echoes_client_request_id(app_with_middleware):
    from app.middleware.request_id import RequestIDMiddleware

    api = FastAPI()
    api.add_middleware(RequestIDMiddleware)

    @api.get("/boom")
    def boom():
        raise RuntimeError("downstream blew up")

    client = TestClient(api, raise_server_exceptions=False)
    r = client.get("/boom", headers={"X-Request-ID": "incident-42"})
    assert r.status_code == 500
    assert r.headers.get("x-request-id") == "incident-42"

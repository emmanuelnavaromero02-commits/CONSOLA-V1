from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path

import requests


def _import_client():
    root = str(Path(__file__).resolve().parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    from app.core import sap_client

    sap_client._make_retry_session = lambda *args, **kwargs: requests.Session()
    return sap_client


def _server(status: int, body: bytes = b"{}"):
    calls = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls["count"] += 1
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


def test_sap_hcm_health_probe_reaches_mock_server_auth_error(monkeypatch):
    server, calls = _server(401)
    monkeypatch.setenv("SAP_HCM_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("SAP_HCM_USER", "hcm-user")
    monkeypatch.setenv("SAP_HCM_PASS", "hcm-pass")
    sap_client = _import_client()
    CartridgeCircuitBreaker = sap_client.CartridgeCircuitBreaker
    SapHcmClient = sap_client.SapHcmClient

    CartridgeCircuitBreaker.reset()
    try:
        result = SapHcmClient().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "auth_error"
    assert result["reachable"] is True
    assert result["circuit_breaker"]["state"] == "HEALTHY"
    assert calls["count"] == 1


def test_sap_hcm_circuit_breaker_blocks_after_three_failures(monkeypatch):
    server, calls = _server(503)
    monkeypatch.setenv("SAP_HCM_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("SAP_HCM_USER", "hcm-user")
    monkeypatch.setenv("SAP_HCM_PASS", "hcm-pass")
    sap_client = _import_client()
    CartridgeCircuitBreaker = sap_client.CartridgeCircuitBreaker
    SapHcmClient = sap_client.SapHcmClient

    CartridgeCircuitBreaker.reset()
    try:
        for _ in range(3):
            assert SapHcmClient().test_connection()["status"] == "error"
        result = SapHcmClient().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "unhealthy"
    assert result["circuit_breaker"]["state"] == "UNHEALTHY"
    assert calls["count"] == 3

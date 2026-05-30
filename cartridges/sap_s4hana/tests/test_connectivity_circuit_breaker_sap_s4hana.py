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


def test_sap_s4hana_health_probe_reaches_mock_server_auth_error(monkeypatch):
    calls = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls["count"] += 1
            self.send_response(403)
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SAP_S4_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("SAP_S4_USER", "s4-user")
    monkeypatch.setenv("SAP_S4_PASS", "s4-pass")
    sap_client = _import_client()
    CartridgeCircuitBreaker = sap_client.CartridgeCircuitBreaker
    SapS4Client = sap_client.SapS4Client

    CartridgeCircuitBreaker.reset()
    try:
        result = SapS4Client().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "auth_error"
    assert result["reachable"] is True
    assert result["circuit_breaker"]["state"] == "HEALTHY"
    assert calls["count"] == 1

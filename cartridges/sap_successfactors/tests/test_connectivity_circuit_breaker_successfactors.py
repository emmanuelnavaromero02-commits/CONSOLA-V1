from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def test_successfactors_health_probe_runs_oauth_and_metadata_handshake(monkeypatch):
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            calls.append("POST " + self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"access_token": "token", "expires_in": 3600}).encode("utf-8"))

        def do_GET(self):  # noqa: N802
            calls.append("GET " + self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"d":{}}')

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("SF_BASE_URL", base)
    monkeypatch.setenv("SF_TOKEN_URL", f"{base}/oauth/token")
    monkeypatch.setenv("SF_CLIENT_ID", "sf-client")
    monkeypatch.setenv("SF_CLIENT_SECRET", "sf-secret")
    monkeypatch.setenv("SF_COMPANY_ID", "sf-company")
    sap_client = _import_client()
    CartridgeCircuitBreaker = sap_client.CartridgeCircuitBreaker
    SapSfClient = sap_client.SapSfClient

    CartridgeCircuitBreaker.reset()
    try:
        result = SapSfClient().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "ok", result
    assert result["reachable"] is True
    assert calls == ["POST /oauth/token", "GET /$metadata?%24format=json"]

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _import_client():
    root = str(Path(__file__).resolve().parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    from app.core import replicon_client

    replicon_client._RETRY_ATTEMPTS = 1
    replicon_client._RETRY_BASE_DELAY = 0
    return replicon_client


def _server(status: int, body: object):
    calls = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls["count"] += 1
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, calls


def test_replicon_health_uses_real_http_mock_server(monkeypatch):
    server, calls = _server(200, [{"id": "Project"}])
    monkeypatch.setenv("REPLICON_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("REPLICON_API_TOKEN", "test-token")
    replicon_client = _import_client()
    CartridgeCircuitBreaker = replicon_client.CartridgeCircuitBreaker
    RepliconClient = replicon_client.RepliconClient

    CartridgeCircuitBreaker.reset()
    try:
        result = RepliconClient().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["tables"] == 1
    assert calls["count"] == 1


def test_replicon_circuit_breaker_opens_after_three_remote_failures(monkeypatch):
    server, calls = _server(503, {"error": "upstream down"})
    monkeypatch.setenv("REPLICON_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("REPLICON_API_TOKEN", "test-token")
    replicon_client = _import_client()
    CartridgeCircuitBreaker = replicon_client.CartridgeCircuitBreaker
    RepliconClient = replicon_client.RepliconClient

    CartridgeCircuitBreaker.reset()
    try:
        for _ in range(3):
            assert RepliconClient().test_connection()["status"] == "error"
        result = RepliconClient().test_connection()
    finally:
        server.shutdown()

    assert result["status"] == "unhealthy"
    assert result["circuit_breaker"]["state"] == "UNHEALTHY"
    assert calls["count"] == 3

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


def _import_vault_client():
    root = str(Path(__file__).resolve().parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    from app.core import vault_client

    vault_client._CONNECTION_CACHE.clear()
    return vault_client


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


def test_replicon_vault_helper_reveals_selected_conn_id(monkeypatch):
    vault_client = _import_vault_client()
    requested_urls: list[str] = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "conn_id": "seeded_gold",
                "base_url": "https://replicon.example.test",
                "token": "test-token",
            }

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return Response()

    monkeypatch.setenv("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", "test-key")
    monkeypatch.setenv("CONSOLE_URL", "http://console:8000")
    monkeypatch.setattr(vault_client.requests, "get", fake_get)

    payload = vault_client.get_connection_for_worker("replicon", conn_id="seeded_gold")

    assert payload["conn_id"] == "seeded_gold"
    assert requested_urls == [
        "http://console:8000/api/vault/connections/replicon/seeded_gold/reveal"
    ]


def test_replicon_client_uses_selected_vault_connection(monkeypatch):
    replicon_client = _import_client()
    captured: dict[str, str | None] = {}

    def fake_connection(*, security_context=None, conn_id=None):
        captured["security_context"] = security_context
        captured["conn_id"] = conn_id
        return {
            "base_url": "https://replicon.example.test",
            "token": "test-token",
            "auth_method": "bearer_token",
        }

    monkeypatch.setattr(replicon_client, "get_replicon_connection", fake_connection)

    client = replicon_client.RepliconClient(
        security_context="signed-context",
        conn_id="seeded_gold",
    )

    assert client.base_url == "https://replicon.example.test"
    assert captured == {"security_context": "signed-context", "conn_id": "seeded_gold"}


def test_replicon_seeded_gold_connection_is_data_only_ok(monkeypatch):
    replicon_client = _import_client()
    RepliconClient = replicon_client.RepliconClient

    monkeypatch.setattr(
        replicon_client,
        "get_replicon_connection",
        lambda **_kwargs: {
            "base_url": "seeded://replicon-beta-gold",
            "auth_method": "seeded_gold",
        },
    )

    def fail_if_network_called(*_args, **_kwargs):
        raise AssertionError("seeded_gold must not call external Replicon")

    monkeypatch.setattr(replicon_client.requests, "get", fail_if_network_called)
    result = RepliconClient(conn_id="seeded_gold").test_connection()

    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["data_only"] is True
    assert result["conn_id"] == "seeded_gold"


def test_replicon_seeded_gold_extract_is_data_only_without_network(monkeypatch):
    replicon_client = _import_client()
    RepliconClient = replicon_client.RepliconClient

    monkeypatch.setattr(
        replicon_client,
        "get_replicon_connection",
        lambda **_kwargs: {
            "base_url": "seeded://replicon-beta-gold",
            "auth_method": "seeded_gold",
        },
    )

    def fail_if_network_called(*_args, **_kwargs):
        raise AssertionError("seeded_gold extraction must not call external Replicon")

    monkeypatch.setattr(replicon_client.requests, "get", fail_if_network_called)
    monkeypatch.setattr(replicon_client.requests, "post", fail_if_network_called)

    result = RepliconClient(conn_id="seeded_gold").extract_table("User")

    assert result == []


def test_replicon_dns_failure_reports_configured_host(monkeypatch):
    replicon_client = _import_client()
    RepliconClient = replicon_client.RepliconClient

    monkeypatch.setattr(
        replicon_client,
        "get_replicon_connection",
        lambda **_kwargs: {
            "base_url": "https://bad-replicon-host.invalid",
            "token": "test-token",
            "auth_method": "bearer_token",
        },
    )

    def fail_dns(*_args, **_kwargs):
        raise replicon_client.requests.exceptions.ConnectionError(
            "[Errno -3] Temporary failure in name resolution"
        )

    monkeypatch.setattr(replicon_client.requests, "get", fail_dns)
    result = RepliconClient(conn_id="seeded_gold").test_connection()

    assert result["status"] == "error"
    assert result["reachable"] is False
    assert result["conn_id"] == "seeded_gold"
    assert result["base_url_host"] == "bad-replicon-host.invalid"
    assert result["error"] == "Replicon host could not be resolved by DNS: bad-replicon-host.invalid"


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

from __future__ import annotations

import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
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


def test_saml_bearer_auth_gets_assertion_from_successfactors_idp_and_caches_token(monkeypatch, tmp_path):
    private_key_pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "unit-test-private-key-body\n"
        "-----END PRIVATE KEY-----\n"
    )
    key_path = tmp_path / "sf-test-private-key.pem"
    key_path.write_text(private_key_pem, encoding="utf-8")

    posts: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
            body = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()}
            body["_path"] = self.path
            posts.append(body)
            if self.path == "/oauth/idp":
                payload = b"server-side-signed-saml-assertion"
                content_type = "text/plain"
            else:
                payload = b'{"access_token":"saml-token","expires_in":600}'
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token_url = f"http://127.0.0.1:{server.server_port}/oauth/token"

    monkeypatch.setenv("SF_BASE_URL", f"http://127.0.0.1:{server.server_port}/odata/v2")
    monkeypatch.setenv("SF_TOKEN_URL", token_url)
    monkeypatch.setenv("SF_CLIENT_ID", "sf-client-id")
    monkeypatch.setenv("SF_COMPANY_ID", "sf-company")
    monkeypatch.setenv("SF_ADMIN_USER", "admin@example.com")
    monkeypatch.setenv("SF_PRIVATE_KEY_PATH", str(key_path))

    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {"auth_method": "saml_bearer_assertion"},
    )
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: None)

    try:
        client = sap_client.SapSfClient()
        assert client._get_token() == "saml-token"
        assert client._get_token() == "saml-token"
    finally:
        server.shutdown()

    assert [post["_path"] for post in posts] == ["/oauth/idp", "/oauth/token"]

    idp_body = posts[0]
    assert idp_body["client_id"] == "sf-client-id"
    assert idp_body["user_id"] == "admin@example.com"
    assert idp_body["token_url"] == token_url
    assert idp_body["private_key"] == private_key_pem
    assert "company_id" not in idp_body
    assert "grant_type" not in idp_body

    token_body = posts[1]
    assert token_body["grant_type"] == "urn:ietf:params:oauth:grant-type:saml2-bearer"
    assert token_body["company_id"] == "sf-company"
    assert token_body["client_id"] == "sf-client-id"
    assert token_body["assertion"] == "server-side-signed-saml-assertion"
    assert "client_secret" not in token_body


def test_live_saml_bearer_test_connection_against_configured_successfactors():
    if os.getenv("OMEGA_ENABLE_LIVE_SF_SAML_TEST") != "1":
        pytest.skip("set OMEGA_ENABLE_LIVE_SF_SAML_TEST=1 with real SF SAML credentials")

    required = [
        "SF_BASE_URL",
        "SF_TOKEN_URL",
        "SF_COMPANY_ID",
        "SF_CLIENT_ID",
        "SF_ADMIN_USER",
    ]
    missing = [name for name in required if not os.getenv(name)]
    key_present = bool(os.getenv("SF_PRIVATE_KEY_PEM")) or bool(os.getenv("SF_PRIVATE_KEY_PATH"))
    if missing or not key_present:
        pytest.skip(f"missing live SuccessFactors SAML configuration: {missing or ['SF_PRIVATE_KEY_PEM_OR_PATH']}")

    os.environ["SF_AUTH_METHOD"] = "saml_bearer_assertion"
    sap_client = _import_client()
    client = sap_client.SapSfClient()
    result = client.test_connection()
    assert result["status"] == "ok", result

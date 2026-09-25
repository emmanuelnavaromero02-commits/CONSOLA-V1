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
    assert idp_body["private_key"] == "unit-test-private-key-body"
    assert "BEGIN PRIVATE KEY" not in idp_body["private_key"]
    assert "\n" not in idp_body["private_key"]
    assert "company_id" not in idp_body
    assert "grant_type" not in idp_body

    token_body = posts[1]
    assert token_body["grant_type"] == "urn:ietf:params:oauth:grant-type:saml2-bearer"
    assert token_body["company_id"] == "sf-company"
    assert token_body["client_id"] == "sf-client-id"
    assert token_body["assertion"] == "server-side-signed-saml-assertion"
    assert "client_secret" not in token_body


def test_explicit_vault_connection_auth_method_wins_over_container_default(monkeypatch):
    monkeypatch.setenv("SF_AUTH_METHOD", "oauth2_client_credentials")

    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(
        sap_client,
        "get_secret_for_worker",
        lambda _cart, env_var_name, **_kwargs: os.getenv(env_var_name, ""),
    )

    client = sap_client.SapSfClient(conn_id="tenant_sf", security_context='{"trusted":true}')
    status = client.configuration_status()

    assert client.auth_method == "saml_bearer_assertion"
    assert status["configured"] is True
    assert status["missing"] == []


def test_saml_bearer_token_401_reports_successfactors_rejection(monkeypatch):
    sap_client = _import_client()
    token_url = "https://api68sales.successfactors.com/oauth/token"

    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "successfactors_sfapi",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": token_url,
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: "")

    class RejectingSession:
        def post(self, *_args, **_kwargs):
            response = requests.Response()
            response.status_code = 401
            response.url = token_url
            response._content = b'{"error":"invalid_grant"}'
            return response

    client = sap_client.SapSfClient(conn_id="successfactors_sfapi")
    client._session = RejectingSession()
    monkeypatch.setattr(client, "_request_saml_assertion_from_successfactors", lambda: "assertion")

    with pytest.raises(sap_client.SAPClientError) as exc_info:
        client._request_uncached_saml_bearer_token(("unit-test",))

    message = str(exc_info.value)
    assert "SuccessFactors rechazo la conexion guardada en Vault (HTTP 401)" in message
    assert "autenticacion SAML bearer" in message
    assert "private_key_pem" not in message
    assert "company_id" not in message


def test_vault_admin_user_wins_over_placeholder_extra_username(monkeypatch):
    monkeypatch.setenv("SF_ADMIN_USER", "env-should-not-win")

    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "extra_json": {"username": "user@company.com"},
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(
        sap_client,
        "get_secret_for_worker",
        lambda _cart, env_var_name, **_kwargs: os.getenv(env_var_name, ""),
    )

    client = sap_client.SapSfClient(conn_id="tenant_sf")
    diagnostics = client.sanitized_config_diagnostics()

    assert client.admin_user == "SFAPI"
    assert diagnostics["subject_source"] == "admin_user"
    assert diagnostics["extra_username_placeholder_blocked"] is True
    assert diagnostics["runtime_env"]["SF_ADMIN_USER"]["present"] is True


def test_placeholder_extra_username_is_ignored_and_does_not_default_subject(monkeypatch):
    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "extra_json": {"username": "user@company.com"},
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: "")

    client = sap_client.SapSfClient(conn_id="tenant_sf")
    status = client.configuration_status()
    diagnostics = client.sanitized_config_diagnostics()

    assert client.admin_user == ""
    assert "SF_ADMIN_USER" in status["missing"]
    assert diagnostics["subject_source"] == "missing"
    assert diagnostics["extra_username_placeholder_blocked"] is True


def test_explicit_vault_connection_does_not_mix_missing_fields_from_env(monkeypatch):
    monkeypatch.setenv("SF_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("SF_ADMIN_USER", "env-admin")

    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(
        sap_client,
        "get_secret_for_worker",
        lambda _cart, env_var_name, **_kwargs: os.getenv(env_var_name, ""),
    )

    client = sap_client.SapSfClient(conn_id="tenant_sf")
    status = client.configuration_status()

    assert client.client_id == ""
    assert "SF_CLIENT_ID" in status["missing"]
    assert client.admin_user == "SFAPI"


def test_explicit_vault_connection_missing_auth_method_does_not_default_to_oauth(monkeypatch):
    monkeypatch.setenv("SF_AUTH_METHOD", "saml_bearer_assertion")

    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(
        sap_client,
        "get_secret_for_worker",
        lambda _cart, env_var_name, **_kwargs: os.getenv(env_var_name, ""),
    )

    client = sap_client.SapSfClient(conn_id="tenant_sf")
    status = client.configuration_status()
    diagnostics = client.sanitized_config_diagnostics()

    assert client.auth_method == ""
    assert diagnostics["effective_source"] == "vault"
    assert status["configured"] is False
    assert "SF_AUTH_METHOD" in status["missing"]


def test_missing_explicit_vault_connection_reports_vault_missing(monkeypatch):
    sap_client = _import_client()
    monkeypatch.setattr(sap_client, "get_connection_for_worker", lambda _cart, **_kwargs: {})
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: "")

    client = sap_client.SapSfClient(conn_id="tenant_sf")
    diagnostics = client.sanitized_config_diagnostics()

    assert diagnostics["effective_source"] == "vault_missing"
    assert diagnostics["configured"] is False


def test_idp_url_is_derived_from_token_url_for_vault_connection(monkeypatch):
    sap_client = _import_client()
    monkeypatch.setattr(
        sap_client,
        "get_connection_for_worker",
        lambda _cart, **_kwargs: {
            "conn_id": "tenant_sf",
            "auth_method": "saml_bearer_assertion",
            "base_url": "https://api68sales.successfactors.com",
            "token_url": "https://api68sales.successfactors.com/oauth/token",
            "client_id": "sf-client-id",
            "company_id": "SFCPART000952",
            "admin_user": "SFAPI",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nunit-test\n-----END PRIVATE KEY-----\n",
        },
    )
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: "")

    client = sap_client.SapSfClient(conn_id="tenant_sf")

    assert client.idp_url == "https://api68sales.successfactors.com/oauth/idp"
    assert client.sanitized_config_diagnostics()["private_key_present"] is True


def test_successfactors_idp_private_key_payload_preserves_raw_key(monkeypatch):
    sap_client = _import_client()

    raw = "alreadyRawBase64KeyBody123"

    assert sap_client._successfactors_idp_private_key_payload(raw) == raw
    assert sap_client._successfactors_idp_private_key_payload(" alreadyRawBase64KeyBody123\n") == raw


def test_successfactors_odata_base_url_normalizes_host_root(monkeypatch):
    sap_client = _import_client()

    assert (
        sap_client._normalize_odata_base_url("https://api68sales.successfactors.com")
        == "https://api68sales.successfactors.com/odata/v2"
    )
    assert (
        sap_client._normalize_odata_base_url("https://api68sales.successfactors.com/")
        == "https://api68sales.successfactors.com/odata/v2"
    )
    assert (
        sap_client._normalize_odata_base_url("https://api68sales.successfactors.com/odata/v2")
        == "https://api68sales.successfactors.com/odata/v2"
    )
    assert (
        sap_client._normalize_odata_base_url("https://api68sales.successfactors.com/custom")
        == "https://api68sales.successfactors.com/custom"
    )


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

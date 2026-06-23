from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_vault_client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", "dedicated")
    sys.path[:] = [
        p
        for p in sys.path
        if not any(marker in p for marker in ("/cartridges/", "/console", "/vault", "/workspace", "/mcp-infra", "/refinement"))
    ]
    sys.path.insert(0, str(ROOT))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    return importlib.import_module("app.core.vault_client")


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return dict(self._payload)


def test_explicit_conn_id_reveals_that_vault_connection(monkeypatch):
    client = _load_vault_client(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, {"conn_id": "femsa_sf", "auth_method": "saml_bearer_assertion"})

    monkeypatch.setattr(client.requests, "get", fake_get)

    payload = client.get_connection_for_worker("sap_successfactors", conn_id="femsa_sf")

    assert payload["conn_id"] == "femsa_sf"
    assert payload["auth_method"] == "saml_bearer_assertion"
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/vault/connections/sap_successfactors/femsa_sf/reveal")
    assert calls[0][1]["headers"] == {
        "x-api-key": "dedicated",
        "x-internal-service": "cartridge-sap_successfactors",
    }


def test_scoped_security_context_is_forwarded_to_vault_reveal(monkeypatch):
    client = _load_vault_client(monkeypatch)
    calls: list[tuple[str, dict]] = []
    signed_context = '{"trusted":true,"tenant_id":"b95","workspace_id":"a2","_signature":"sig"}'

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(
            200,
            {
                "conn_id": "femsa_sf",
                "auth_method": "saml_bearer_assertion",
                "base_url": "https://api68sales.successfactors.com",
                "client_id": "client",
                "company_id": "company",
                "admin_user": "SFAPI",
                "private_key_pem": "-----BEGIN PRIVATE KEY-----\\n...",
            },
        )

    monkeypatch.setattr(client.requests, "get", fake_get)

    payload = client.get_connection_for_worker(
        "sap_successfactors",
        conn_id="femsa_sf",
        security_context=signed_context,
    )

    assert payload["auth_method"] == "saml_bearer_assertion"
    assert calls[0][1]["headers"] == {
        "x-api-key": "dedicated",
        "x-internal-service": "cartridge-sap_successfactors",
        "x-security-context": signed_context,
    }


def test_airflow_key_can_reveal_successfactors_vault_connection(monkeypatch):
    client = _load_vault_client(monkeypatch)
    monkeypatch.delenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", "airflow-key")
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, {"conn_id": "femsa_sf", "base_url": "https://example.invalid"})

    monkeypatch.setattr(client.requests, "get", fake_get)

    payload = client.get_connection_for_worker("sap_successfactors", conn_id="femsa_sf")

    assert payload["conn_id"] == "femsa_sf"
    assert calls[0][1]["headers"] == {
        "x-api-key": "airflow-key",
        "x-internal-service": "airflow",
    }


def test_missing_conn_id_keeps_default_then_analytics_fallback(monkeypatch):
    client = _load_vault_client(monkeypatch)
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if url.endswith("/default/reveal"):
            return _Response(404)
        return _Response(200, {"conn_id": "analytics", "base_url": "https://example.invalid"})

    monkeypatch.setattr(client.requests, "get", fake_get)

    payload = client.get_connection_for_worker("sap_successfactors")

    assert payload["conn_id"] == "analytics"
    assert [url.rsplit("/", 2)[-2] for url in calls] == ["default", "analytics"]


def test_explicit_conn_id_rejects_unsafe_values(monkeypatch):
    client = _load_vault_client(monkeypatch)

    with pytest.raises(ValueError, match="invalid Vault connection id"):
        client.get_connection_for_worker("sap_successfactors", conn_id="../../etc/passwd")

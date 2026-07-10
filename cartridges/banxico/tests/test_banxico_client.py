from __future__ import annotations

import logging

import pytest
import requests
import yaml

from app.core.banxico_client import BanxicoClient, BanxicoClientError, MetadataDriftError
from app.core.source_security import SourceSecurityError, validate_url
from app.services.config_loader import load_series_configs
from app.services.preflight_service import validate_metadata


class FakeSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def _response(status: int, payload: bytes, headers: dict[str, str] | None = None) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = payload
    res.headers.update(headers or {})
    res.url = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718"
    return res


def test_token_is_sent_only_as_header():
    session = FakeSession(_response(200, b'{"bmx":{"series":[]}}'))
    client = BanxicoClient(token="secret-token", session=session, sleep=lambda _: None)
    client.get_metadata(["SF43718"])
    call = session.calls[0]
    assert call["headers"]["Bmx-Token"] == "secret-token"
    assert "token" not in call["url"].lower()
    assert "token" not in call["params"]


def test_development_allows_banxico_api_token_without_vault(monkeypatch):
    from app.core import vault_client

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BANXICO_API_TOKEN", "dev-env-token")

    def unexpected_vault_call(*_args, **_kwargs):
        raise AssertionError("Vault should not be called for local env token fallback")

    monkeypatch.setattr(vault_client.requests, "get", unexpected_vault_call)
    session = FakeSession(_response(200, b'{"bmx":{"series":[]}}'))
    client = BanxicoClient(session=session, sleep=lambda _: None)
    client.get_metadata(["SF43718"])

    assert session.calls[0]["headers"]["Bmx-Token"] == "dev-env-token"


def test_production_token_can_be_resolved_from_console_vault(monkeypatch):
    from app.core import vault_client

    monkeypatch.delenv("BANXICO_API_TOKEN", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_BANXICO_TO_CONSOLE", "banxico-console-key")
    monkeypatch.setenv("CONSOLE_URL", "http://console.test")
    captured: dict[str, object] = {}

    class VaultResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"token": "vault-token"}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return VaultResponse()

    monkeypatch.setattr(vault_client.requests, "get", fake_get)
    session = FakeSession(_response(200, b'{"bmx":{"series":[]}}'))
    client = BanxicoClient(
        conn_id="default",
        security_context='{"signed":true}',
        session=session,
        sleep=lambda _: None,
    )
    client.get_metadata(["SF43718"])

    assert captured["url"] == "http://console.test/api/vault/connections/banxico/default/reveal"
    assert captured["headers"] == {
        "x-api-key": "banxico-console-key",
        "x-internal-service": "banxico",
        "x-security-context": '{"signed":true}',
    }
    assert session.calls[0]["headers"]["Bmx-Token"] == "vault-token"


def test_production_rejects_env_token_without_vault(monkeypatch):
    from app.core import vault_client

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BANXICO_API_TOKEN", "prod-env-token")
    monkeypatch.delenv("INTERNAL_API_KEY_BANXICO_TO_CONSOLE", raising=False)

    with pytest.raises(RuntimeError) as exc:
        BanxicoClient(conn_id="default", session=FakeSession(_response(200, b"{}")))

    assert "Vault" in str(exc.value)
    assert "prod-env-token" not in str(exc.value)


def test_production_without_token_has_clear_error(monkeypatch):
    from app.core import vault_client

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("BANXICO_API_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY_BANXICO_TO_CONSOLE", raising=False)

    with pytest.raises(RuntimeError, match="Vault conn_id is required"):
        BanxicoClient(session=FakeSession(_response(200, b"{}")))


def test_production_secret_not_in_logs_errors_or_http_response(monkeypatch, caplog):
    from app.api.routes_skills import test_connection
    from app.core import vault_client

    secret = "prod-env-secret"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BANXICO_API_TOKEN", secret)
    monkeypatch.setenv("INTERNAL_API_KEY_BANXICO_TO_CONSOLE", "banxico-console-key")
    monkeypatch.setenv("CONSOLE_URL", "http://console.test")
    caplog.set_level(logging.DEBUG, logger="app.core.vault_client")

    def fake_get(*_args, **_kwargs):
        raise RuntimeError(f"vault transport failed with {secret}")

    monkeypatch.setattr(vault_client.requests, "get", fake_get)
    with pytest.raises(RuntimeError) as exc:
        BanxicoClient(conn_id="default", session=FakeSession(_response(200, b"{}")))
    response = test_connection(conn_id="default", x_security_context=None)
    body = response.body.decode("utf-8")

    assert secret not in str(exc.value)
    assert secret not in caplog.text
    assert secret not in body
    assert "RuntimeError" in body


def test_http_and_bad_host_are_rejected():
    with pytest.raises(SourceSecurityError):
        validate_url("http://www.banxico.org.mx/SieAPIRest/service/v1")
    with pytest.raises(SourceSecurityError):
        validate_url("https://evil.example/SieAPIRest/service/v1")


def test_banxico_rate_limit_400_uses_reset_header():
    session = FakeSession(
        _response(
            400,
            b'{"error":{"secondsToReset":55}}',
            {"Bmx-secondsToReset": "55"},
        )
    )
    client = BanxicoClient(token="secret-token", session=session, sleep=lambda _: None)
    with pytest.raises(BanxicoClientError) as exc:
        client.get_metadata(["SF43718"])
    assert "secret-token" not in str(exc.value)
    assert len(session.calls) == 3


def test_preflight_fails_when_metadata_title_changes():
    series = load_series_configs()

    class Client:
        def get_metadata(self, _ids):
            return {"bmx": {"series": [{"idSerie": series[0].series_id, "titulo": "changed"}]}}

    with pytest.raises(MetadataDriftError):
        validate_metadata(Client(), series[:1])


def test_preflight_fails_when_series_is_missing():
    series = load_series_configs()

    class Client:
        def get_metadata(self, _ids):
            return {"bmx": {"series": []}}

    with pytest.raises(MetadataDriftError):
        validate_metadata(Client(), series[:1])


def test_sp74665_is_not_in_initial_config():
    ids = {item.series_id for item in load_series_configs()}
    assert "SP74665" not in ids


def test_entities_yaml_marks_live_preflight_as_pending():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app" / "config" / "entities.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    observations = config["entities"][0]
    assert observations["metadata_preflight_status"] == "pending_live_banxico_token"
    assert "expected_title" in observations["pending_official_validation"]
    assert all(
        item["official_preflight_status"] == "pending_live_banxico_token"
        for item in observations["series"]
    )

from __future__ import annotations

import json
import logging

import pytest
import requests
import yaml

from app.core.inegi_client import INEGIClient, INEGIClientError, MetadataDriftError
from app.core.source_security import SourceSecurityError, validate_url
from app.services.config_loader import load_series_configs
from app.services.preflight_service import validate_metadata


class QueueSession:
    def __init__(self, *responses: requests.Response) -> None:
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected INEGI request")
        return self.responses.pop(0)


def _response(status: int, payload: bytes, headers: dict[str, str] | None = None) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = payload
    res.headers.update(headers or {})
    res.url = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR/454168/es/00/true/BISE/2.0/token"
    return res


def _catalog(title: str) -> requests.Response:
    return _response(200, f'{{"CODE":[{{"Description":"{title}"}}]}}'.encode())


def _series() -> requests.Response:
    return _response(
        200,
        b'{"Series":[{"INDICADOR":"454168","UNIT":"index","FREQ":"M","LASTUPDATE":"2026-06-30","SOURCE":"BISE","OBSERVATIONS":[]}]}',
    )


def test_token_uses_official_path_segment_and_source_url_is_redacted():
    session = QueueSession(_catalog("Indicador global de la actividad economica, base 2018"), _series())
    client = INEGIClient(token="secret-token", session=session, sleep=lambda _: None)
    client.get_metadata(["454168"])

    call = session.calls[0]
    assert "/secret-token" in call["url"]
    assert "token" not in call["headers"]
    assert "secret-token" not in client.source_url("454168")
    assert "redacted" in client.source_url("454168")


def test_development_allows_inegi_api_token_without_vault(monkeypatch):
    from app.core import vault_client

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INEGI_API_TOKEN", "dev-env-token")

    def unexpected_vault_call(*_args, **_kwargs):
        raise AssertionError("Vault should not be called for local env token fallback")

    monkeypatch.setattr(vault_client.requests, "get", unexpected_vault_call)
    session = QueueSession(_catalog("Indicador global de la actividad economica, base 2018"), _series())
    client = INEGIClient(session=session, sleep=lambda _: None)
    client.get_metadata(["454168"])

    assert "/dev-env-token" in session.calls[0]["url"]


def test_production_token_can_be_resolved_from_console_vault(monkeypatch):
    from app.core import vault_client

    monkeypatch.delenv("INEGI_API_TOKEN", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_INEGI_TO_CONSOLE", "inegi-console-key")
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
    session = QueueSession(_catalog("Indicador global de la actividad economica, base 2018"), _series())
    client = INEGIClient(
        conn_id="default",
        security_context='{"signed":true}',
        session=session,
        sleep=lambda _: None,
    )
    client.get_metadata(["454168"])

    assert captured["url"] == "http://console.test/api/vault/connections/inegi/default/reveal"
    assert captured["headers"] == {
        "x-api-key": "inegi-console-key",
        "x-internal-service": "inegi",
        "x-security-context": '{"signed":true}',
    }
    assert "/vault-token" in session.calls[0]["url"]


def test_production_rejects_env_token_without_vault(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INEGI_API_TOKEN", "prod-env-token")
    monkeypatch.delenv("INTERNAL_API_KEY_INEGI_TO_CONSOLE", raising=False)

    with pytest.raises(RuntimeError) as exc:
        INEGIClient(conn_id="default", session=QueueSession(_response(200, b"{}")))

    assert "Vault" in str(exc.value)
    assert "prod-env-token" not in str(exc.value)


def test_production_without_token_has_clear_error(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("INEGI_API_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY_INEGI_TO_CONSOLE", raising=False)

    with pytest.raises(RuntimeError, match="Vault conn_id is required"):
        INEGIClient(session=QueueSession(_response(200, b"{}")))


def test_production_secret_not_in_logs_errors_or_http_response(monkeypatch, caplog):
    from app.api.routes_skills import test_connection
    from app.core import vault_client

    secret = "prod-env-secret"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INEGI_API_TOKEN", secret)
    monkeypatch.setenv("INTERNAL_API_KEY_INEGI_TO_CONSOLE", "inegi-console-key")
    monkeypatch.setenv("CONSOLE_URL", "http://console.test")
    caplog.set_level(logging.DEBUG, logger="app.core.vault_client")

    def fake_get(*_args, **_kwargs):
        raise RuntimeError(f"vault transport failed with {secret}")

    monkeypatch.setattr(vault_client.requests, "get", fake_get)
    with pytest.raises(RuntimeError) as exc:
        INEGIClient(conn_id="default", session=QueueSession(_response(200, b"{}")))
    response = test_connection(conn_id="default", x_security_context=None)
    body = response.body.decode("utf-8")

    assert secret not in str(exc.value)
    assert secret not in caplog.text
    assert secret not in body
    assert "RuntimeError" in body


def test_http_and_bad_host_are_rejected():
    with pytest.raises(SourceSecurityError):
        validate_url("http://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml")
    with pytest.raises(SourceSecurityError):
        validate_url("https://evil.example/app/api/indicadores/desarrolladores/jsonxml")


def test_inegi_rate_limit_400_uses_reset_header():
    session = QueueSession(
        _response(
            400,
            b'{"error":{"secondsToReset":55}}',
            {"Retry-After": "55"},
        ),
        _response(400, b'{"error":{"secondsToReset":55}}', {"Retry-After": "55"}),
        _response(400, b'{"error":{"secondsToReset":55}}', {"Retry-After": "55"}),
    )
    client = INEGIClient(token="secret-token", session=session, sleep=lambda _: None)
    with pytest.raises(INEGIClientError) as exc:
        client.get_metadata(["454168"])
    assert "secret-token" not in str(exc.value)
    assert len(session.calls) == 3


def test_inegi_error_payload_is_reported_without_token():
    session = QueueSession(
        _response(401, b'["ErrorInfo:No autorizado","ErrorDetails:No autorizado","ErrorCode:110"]')
    )
    client = INEGIClient(token="secret-token", session=session, sleep=lambda _: None)

    with pytest.raises(INEGIClientError) as exc:
        client.get_metadata(["454168"])

    assert "INEGI error 110: No autorizado" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_test_connection_returns_safe_error_message(monkeypatch):
    from app.api import routes_skills

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INEGI_API_TOKEN", "dev-env-token")

    def fail_metadata(*_args, **_kwargs):
        raise INEGIClientError("INEGI error 110: No autorizado")

    monkeypatch.setattr(routes_skills, "validate_metadata", fail_metadata)
    response = routes_skills.test_connection(conn_id="default", x_security_context=None)
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["error"] == "INEGIClientError"
    assert body["message"] == "INEGI error 110: No autorizado"


def test_test_connection_scrubs_secret_like_error(monkeypatch):
    from app.api import routes_skills

    secret = "abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INEGI_API_TOKEN", "dev-env-token")

    def fail_metadata(*_args, **_kwargs):
        raise RuntimeError(f"bad token={secret}")

    monkeypatch.setattr(routes_skills, "validate_metadata", fail_metadata)
    response = routes_skills.test_connection(conn_id="default", x_security_context=None)
    body = response.body.decode("utf-8")

    assert secret not in body
    assert "<redacted>" in body


def test_preflight_fails_when_metadata_title_changes():
    series = load_series_configs()

    class Client:
        def get_metadata(self, _ids):
            return {"inegi": {"metadata": [{"id": series[0].series_id, "title": "changed"}]}}

    with pytest.raises(MetadataDriftError):
        validate_metadata(Client(), series[:1])


def test_preflight_accepts_official_title_whitespace_variation():
    series = load_series_configs()
    expected = series[0]

    class Client:
        def get_metadata(self, _ids):
            spaced = "   ".join(expected.expected_title.split(" "))
            return {"inegi": {"metadata": [{"id": expected.series_id, "title": spaced}]}}

    evidence = validate_metadata(Client(), series[:1])

    assert evidence[0]["series_id"] == expected.series_id


def test_preflight_fails_when_series_is_missing():
    series = load_series_configs()

    class Client:
        def get_metadata(self, _ids):
            return {"inegi": {"metadata": []}}

    with pytest.raises(MetadataDriftError):
        validate_metadata(Client(), series[:1])


def test_entities_yaml_marks_live_preflight_as_pending():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app" / "config" / "entities.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    observations = config["entities"][0]
    assert observations["metadata_preflight_status"] == "pending_live_inegi_token"
    assert "expected_title" in observations["pending_official_validation"]
    assert all(
        item["official_preflight_status"] == "pending_live_inegi_token"
        for item in observations["series"]
    )

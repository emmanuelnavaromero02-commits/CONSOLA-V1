from __future__ import annotations

import json
import re

import pytest
from starlette.requests import Request

from app.services.mcp_gateway import catalog, openapi
from app.services.mcp_gateway.errors import GatewayError
from ia_gateway_fixtures import GatewayHarness, gateway  # noqa: F401


PUBLIC_BASE = "https://consola.example.test"


def _request(host: str = "interno.example.test") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": (host, 443),
            "path": "/api/ia/v1/openapi.json",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
    )


def test_server_url_prefers_the_configured_public_base(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", PUBLIC_BASE + "/")
    assert openapi.server_url(_request()) == PUBLIC_BASE + "/api/ia/v1"


def test_server_url_falls_back_to_console_url(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setenv("CONSOLE_URL", PUBLIC_BASE)
    assert openapi.server_url(_request()) == PUBLIC_BASE + "/api/ia/v1"


def test_server_url_uses_the_request_host_only_outside_production(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("CONSOLE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    assert openapi.server_url(_request("atacante.example.test")) == "https://atacante.example.test/api/ia/v1"
    for env in ("production", "prod", "staging"):
        monkeypatch.setenv("APP_ENV", env)
        with pytest.raises(GatewayError) as exc:
            openapi.server_url(_request("atacante.example.test"))
        assert exc.value.status_code == 503
        assert exc.value.mensaje == "La pasarela no tiene configurada su dirección pública."


def test_document_is_public_and_ignores_tokens(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", PUBLIC_BASE)
    response = gateway.client.get("/api/ia/v1/openapi.json")
    assert response.status_code == 200
    token = gateway.issue()
    with_token = gateway.client.get("/api/ia/v1/openapi.json", headers=gateway.bearer(token))
    assert with_token.status_code == 200
    assert gateway.resolve_calls == []
    assert response.json()["servers"] == [{"url": PUBLIC_BASE + "/api/ia/v1"}]


def test_document_limits_and_shape(monkeypatch):
    doc = openapi.build_document(url=PUBLIC_BASE + "/api/ia/v1")
    assert doc["openapi"] == "3.1.0"
    assert len(json.dumps(doc, ensure_ascii=False).encode()) < 64 * 1024
    assert doc["components"]["securitySchemes"]["tokenPersonal"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "omega_pat",
        "description": "Token personal creado en Mi acceso.",
    }
    assert doc["security"] == [{"tokenPersonal": []}]
    assert len(doc["info"]["description"]) <= 300
    operations = [
        (path, method, operation)
        for path, item in doc["paths"].items()
        for method, operation in item.items()
    ]
    assert len(operations) == len(catalog.ACTIONS) <= 30
    ids = [operation["operationId"] for _path, _method, operation in operations]
    assert len(ids) == len(set(ids))
    assert "/actions/execute" not in doc["paths"]
    for path, method, operation in operations:
        assert method == "post"
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", operation["operationId"])
        assert path == f"/actions/{operation['operationId']}"
        assert len(operation["description"]) <= 300
        assert len(operation["summary"]) <= 300
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert schema["additionalProperties"] is False
        action = catalog.get_action(operation["operationId"])
        assert operation["x-openai-isConsequential"] is (not action.read_only)
        assert set(operation["responses"]) >= {"200", "400", "401", "403", "429", "500", "504"}


def test_read_only_document_hides_actions(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", PUBLIC_BASE)
    doc = gateway.client.get("/api/ia/v1/openapi.json", params={"alcance": "lectura"}).json()
    ids = {item["post"]["operationId"] for item in doc["paths"].values()}
    assert "ejecutar_extraccion" not in ids and "crear_app_analitica" not in ids
    assert "consultar_contexto" in ids
    assert all(item["post"]["x-openai-isConsequential"] is False for item in doc["paths"].values())


def test_production_without_public_url_answers_spanish_503(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("CONSOLE_URL", raising=False)
    response = gateway.client.get("/api/ia/v1/openapi.json")
    assert response.status_code == 503
    assert response.json()["error"]["mensaje"] == "La pasarela no tiene configurada su dirección pública."

from __future__ import annotations

import json
import logging

from app.logging_config import JSONFormatter, SecretRedactionFilter
from app.services import access_tokens
from app.services.mcp_gateway import adapters
from ia_gateway_fixtures import GatewayHarness, gateway  # noqa: F401


def _server_log_text(caplog) -> str:
    return "\n".join(
        record.getMessage() + " " + json.dumps(record.__dict__, default=str)
        for record in caplog.records
        if not record.name.startswith("httpx")
    )


def test_token_and_hash_never_reach_logs_audit_or_responses(gateway: GatewayHarness, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)

    async def failing(user):
        raise RuntimeError("fallo inesperado")

    monkeypatch.setattr(adapters, "control_room_summary", failing)
    token = gateway.issue(scopes=("acciones", "lectura"))
    digest = access_tokens.hash_token(token)
    headers = gateway.bearer(token)
    bodies = [
        gateway.client.get("/api/ia/v1/whoami", headers=headers),
        gateway.client.get("/api/ia/v1/actions", headers=headers),
        gateway.client.post("/api/ia/v1/actions/consultar_contexto", json={}, headers=headers),
        gateway.client.post("/api/ia/v1/actions/consultar_control_room_resumen", json={}, headers=headers),
        gateway.client.post("/api/ia/v1/actions/buscar_documentos_empresa", json={"consulta": "x"}, headers=headers),
        gateway.client.post(
            "/api/ia/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "consultar_contexto", "arguments": {}}},
            headers=headers,
        ),
        gateway.client.get("/api/datasets", headers=headers),
        gateway.client.get("/api/ia/v1/whoami", headers=gateway.bearer(token[:-1] + "Z")),
    ]
    responses = "\n".join(response.text + json.dumps(dict(response.headers)) for response in bodies)
    audit = json.dumps(gateway.audit, default=str)
    logs = _server_log_text(caplog)
    for secret in (token, digest):
        assert secret not in responses
        assert secret not in audit
        assert secret not in logs
    assert any(event["action"] == "ia_gateway.action" for event in gateway.audit)
    assert all(event["metadata"]["token_prefix"] == token[:14] for event in gateway.audit)


def test_redaction_filter_masks_personal_tokens():
    token = access_tokens.generate_token()
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "vi el token %s en %s", (token, "algo"), None)
    SecretRedactionFilter().filter(record)
    rendered = JSONFormatter("console").format(record)
    assert token not in rendered
    assert "omega_pat_***REDACTED***" in rendered
    plain = logging.LogRecord("app", logging.INFO, __file__, 1, f"cabecera {token}", None, None)
    SecretRedactionFilter().filter(plain)
    assert token not in plain.getMessage()

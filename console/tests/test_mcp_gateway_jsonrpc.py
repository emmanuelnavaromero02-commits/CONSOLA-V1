from __future__ import annotations

import asyncio
import json

import pytest

from app.services.mcp_gateway import adapters, dispatcher
from ia_gateway_fixtures import GatewayHarness, gateway  # noqa: F401


def _rpc(gateway: GatewayHarness, token: str, payload, *, headers: dict | None = None):
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return gateway.client.post(
        "/api/ia/v1/mcp",
        content=body,
        headers={
            **gateway.bearer(token),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        },
    )


def _message(method: str, params: dict | None = None, request_id=1) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"), ("2025-03-26", "2025-03-26"), ("2024-11-05", "2025-11-25"), (None, "2025-11-25")],
)
def test_initialize_negotiates_the_protocol_version(gateway: GatewayHarness, requested, expected):
    token = gateway.issue()
    params = {"capabilities": {}, "clientInfo": {"name": "prueba", "version": "1"}}
    if requested:
        params["protocolVersion"] = requested
    response = _rpc(gateway, token, _message("initialize", params))
    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers
    result = response.json()["result"]
    assert result["protocolVersion"] == expected
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "omega"
    assert result["serverInfo"]["version"]
    assert "español" in result["instructions"]


def test_notifications_and_client_responses_are_accepted_without_body(gateway: GatewayHarness):
    token = gateway.issue()
    response = _rpc(gateway, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    assert response.content == b""
    response = _rpc(gateway, token, {"jsonrpc": "2.0", "id": 9, "result": {}})
    assert response.status_code == 202


def test_ping_and_unknown_methods(gateway: GatewayHarness):
    token = gateway.issue()
    assert _rpc(gateway, token, _message("ping")).json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    error = _rpc(gateway, token, _message("resources/list", request_id="a")).json()
    assert error["id"] == "a"
    assert error["error"]["code"] == -32601


def test_tools_list_is_filtered_and_annotated(gateway: GatewayHarness):
    token = gateway.issue(scopes=("lectura",))
    tools = _rpc(gateway, token, _message("tools/list")).json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert "consultar_contexto" in names
    assert not names & {"ejecutar_extraccion", "crear_app_analitica"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["openWorldHint"] is False
        assert set(tool["annotations"]) == {"title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    writer = gateway.issue(scopes=("acciones", "lectura"))
    tools = _rpc(gateway, writer, _message("tools/list")).json()["result"]["tools"]
    annotations = {tool["name"]: tool["annotations"] for tool in tools}
    assert annotations["ejecutar_extraccion"]["readOnlyHint"] is False
    assert annotations["crear_app_analitica"]["idempotentHint"] is False


def test_tools_call_returns_structured_content(gateway: GatewayHarness):
    token = gateway.issue()
    response = _rpc(gateway, token, _message("tools/call", {"name": "consultar_contexto", "arguments": {}}))
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["alcances"] == ["lectura"]
    envelope = json.loads(result["content"][0]["text"])
    assert envelope["ok"] is True and envelope["accion"] == "consultar_contexto"


def test_tools_call_errors_are_spanish_tool_errors(gateway: GatewayHarness):
    token = gateway.issue(scopes=("lectura",))
    response = _rpc(gateway, token, _message("tools/call", {"name": "ejecutar_extraccion", "arguments": {"fuente": "sap b1"}}))
    result = response.json()["result"]
    assert result["isError"] is True
    envelope = json.loads(result["content"][0]["text"])
    assert envelope["error"]["codigo"] == "alcance_insuficiente"
    bad_args = _rpc(gateway, token, _message("tools/call", {"name": "buscar_documentos_empresa", "arguments": {"consulta": 5}}))
    assert bad_args.json()["result"]["isError"] is True
    assert "Argumentos inválidos" in bad_args.json()["result"]["content"][0]["text"]


def test_unknown_tool_and_bad_params_are_invalid_params(gateway: GatewayHarness):
    token = gateway.issue()
    for params in ({"name": "borrar_todo"}, {"name": 5}, {"name": "consultar_contexto", "arguments": [1]}):
        error = _rpc(gateway, token, _message("tools/call", params)).json()["error"]
        assert error["code"] == -32602
    assert _rpc(gateway, token, _message("tools/list", params=[])).json()["error"]["code"] == -32602


def test_batches_bad_json_and_bad_envelopes(gateway: GatewayHarness):
    token = gateway.issue()
    batch = _rpc(gateway, token, [_message("ping"), _message("ping", request_id=2)])
    assert batch.status_code == 400
    assert batch.json()["error"]["code"] == -32600
    parse = _rpc(gateway, token, b"{no es json")
    assert parse.status_code == 400
    assert parse.json()["error"]["code"] == -32700
    for payload in ({"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}, _message("ping", request_id=True), _message("ping", request_id=None), "3"):
        response = _rpc(gateway, token, payload)
        assert response.status_code == 400, payload
        assert response.json()["error"]["code"] == -32600


def test_get_and_delete_are_not_allowed(gateway: GatewayHarness):
    token = gateway.issue()
    for method in ("GET", "DELETE"):
        response = gateway.client.request(method, "/api/ia/v1/mcp", headers=gateway.bearer(token))
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"
        assert response.json()["error"]["codigo"] == "metodo_no_permitido"


def test_origin_and_protocol_headers_are_enforced(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://consola.example.test")
    token = gateway.issue()
    blocked = _rpc(gateway, token, _message("ping"), headers={"Origin": "https://maligno.example.test"})
    assert blocked.status_code == 403
    allowed = _rpc(gateway, token, _message("ping"), headers={"Origin": "https://consola.example.test"})
    assert allowed.status_code == 200
    unsupported = _rpc(gateway, token, _message("ping"), headers={"MCP-Protocol-Version": "2023-01-01"})
    assert unsupported.status_code == 400
    supported = _rpc(gateway, token, _message("ping"), headers={"MCP-Protocol-Version": "2025-06-18"})
    assert supported.status_code == 200


def test_mcp_requires_a_personal_token(gateway: GatewayHarness):
    response = gateway.client.post("/api/ia/v1/mcp", json=_message("ping"))
    assert response.status_code == 401
    assert response.json()["error"]["codigo"] == "token_invalido"


def _sse_events(text: str) -> tuple[int, list[dict]]:
    keepalives = text.count(": keep-alive")
    messages = [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    return keepalives, messages


def test_long_running_calls_stream_sse_keepalives(gateway: GatewayHarness, monkeypatch):
    monkeypatch.setattr(dispatcher, "KEEPALIVE_SECONDS", 0.01)

    async def start(user, *, cartridge, request_id):
        await asyncio.sleep(0.05)
        return {"run_id": "r1", "status": "running", "active": True}

    async def stored(user, *, cartridge, run_id):
        return None

    monkeypatch.setattr(adapters, "start_sync", start)
    monkeypatch.setattr(adapters, "sync_run_origin", stored)
    token = gateway.issue(scopes=("acciones", "lectura"))
    response = _rpc(gateway, token, _message("tools/call", {"name": "ejecutar_extraccion", "arguments": {"fuente": "sap b1"}}, request_id=7))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    keepalives, messages = _sse_events(response.text)
    assert keepalives >= 1
    assert len(messages) == 1
    assert messages[0]["id"] == 7
    assert messages[0]["result"]["isError"] is False
    assert messages[0]["result"]["structuredContent"]["ejecucion_id"] == "r1"
    plain = gateway.client.post(
        "/api/ia/v1/mcp",
        json=_message("tools/call", {"name": "ejecutar_extraccion", "arguments": {"fuente": "sap b1"}}),
        headers={**gateway.bearer(token), "Accept": "application/json"},
    )
    assert plain.headers["content-type"].startswith("application/json")
    assert plain.json()["result"]["isError"] is False


def _nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


@pytest.mark.parametrize("depth", [12_000, 16_000])
def test_deeply_nested_json_is_a_parse_error_not_a_500(gateway: GatewayHarness, depth):
    token = gateway.issue()
    response = _rpc(gateway, token, _nested(depth))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700
    wrapped = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":' + _nested(depth) + b"}"
    response = _rpc(gateway, token, wrapped)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


@pytest.mark.parametrize("depth", [3_000, 8_000])
def test_deep_json_the_parser_accepts_is_still_a_clean_json_rpc_error(gateway: GatewayHarness, depth):
    token = gateway.issue()
    response = _rpc(gateway, token, _nested(depth))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600
    wrapped = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":' + _nested(depth) + b"}"
    response = _rpc(gateway, token, wrapped)
    assert response.json()["error"]["code"] == -32602


@pytest.mark.parametrize("depth", [20, 400, 3_000, 8_000])
def test_nested_arguments_below_the_parser_limit_are_rejected_cleanly(gateway: GatewayHarness, depth):
    token = gateway.issue(scopes=("lectura",))
    for name in ("listar_tablas_disponibles", "ejecutar_extraccion"):
        body = (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"'
            + name.encode()
            + b'","arguments":{"fuente":'
            + _nested(depth)
            + b"}}}"
        )
        response = _rpc(gateway, token, body)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["isError"] is True
        envelope = json.loads(result["content"][0]["text"])
        assert envelope["error"]["codigo"] in {"argumentos_invalidos", "alcance_insuficiente"}

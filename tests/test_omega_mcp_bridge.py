from __future__ import annotations

import importlib.util
import json
import logging
import secrets
import string
import sys
from pathlib import Path

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session


REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "scripts" / "omega_mcp_bridge" / "omega_mcp_bridge.py"
BASE = "https://consola.example.test"


def _load():
    spec = importlib.util.spec_from_file_location("omega_mcp_bridge_under_test", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load()


@pytest.fixture(autouse=True)
def _restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore", "mcp")}
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for name, value in levels.items():
        logging.getLogger(name).setLevel(value)


def _token() -> str:
    return "omega_pat_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(43))


def _config(token: str | None = None, *, read_only: bool = False, timeout: float = 30.0):
    return bridge.BridgeConfig(base_url=BASE, token=token or _token(), timeout_seconds=timeout, read_only=read_only)


ACTIONS = [
    {
        "nombre": "consultar_contexto",
        "titulo": "Consultar contexto",
        "descripcion": "Contexto del token.",
        "alcance": "lectura",
        "esquema_de_entrada": {"type": "object", "properties": {}, "additionalProperties": False},
        "anotaciones": {"title": "Consultar contexto", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "nombre": "ejecutar_extraccion",
        "titulo": "Ejecutar extracción",
        "descripcion": "Inicia una extracción.",
        "alcance": "acciones",
        "esquema_de_entrada": {"type": "object", "properties": {"fuente": {"type": "string", "maxLength": 60}}, "required": ["fuente"]},
        "anotaciones": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


def _gateway(token: str, requests: list[httpx.Request], *, execute_body: str | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {token}"
        path = request.url.path
        if status != 200:
            return httpx.Response(status, json={"ok": False, "accion": None, "error": {"codigo": "x", "mensaje": "Mensaje del servidor.", "reintentable": False}})
        if path == "/api/ia/v1/actions":
            return httpx.Response(200, json={"ok": True, "accion": "listar_acciones", "resumen": "", "datos": {"acciones": ACTIONS}, "truncado": False})
        if path == "/api/ia/v1/whoami":
            return httpx.Response(200, json={"ok": True, "accion": "consultar_identidad", "espacio_de_trabajo": {"id": "w", "nombre": "Operaciones"}, "resumen": "", "datos": {"alcances": ["lectura"], "token": {"vence_en": "2026-12-31"}}, "truncado": False})
        if path == "/api/ia/v1/actions/execute":
            body = json.loads(request.content)
            if execute_body is not None:
                return httpx.Response(200, text=execute_body, headers={"content-type": "application/json"})
            return httpx.Response(200, json={"ok": True, "accion": body["accion"], "resumen": "listo", "datos": {"eco": body["argumentos"]}, "truncado": False})
        return httpx.Response(404, json={"ok": False, "error": {"codigo": "no_encontrado", "mensaje": "No existe.", "reintentable": False}})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"OMEGA_BASE_URL": "http://consola.example.test"}, "https"),
        ({"OMEGA_BASE_URL": "ftp://consola.example.test"}, "OMEGA_BASE_URL"),
        ({"OMEGA_BASE_URL": BASE + "/api"}, "sin ruta"),
        ({"OMEGA_BASE_URL": "https://usuario:clave@consola.example.test"}, "usuario"),
        ({"OMEGA_BASE_URL": BASE}, "OMEGA_API_KEY"),
        ({"OMEGA_BASE_URL": BASE, "OMEGA_API_KEY": "omega_pat_corto"}, "formato"),
        ({"OMEGA_BASE_URL": BASE, "OMEGA_API_KEY": "__TOKEN__", "OMEGA_TIMEOUT_SECONDS": "5"}, "entre 10 y 600"),
        ({"OMEGA_BASE_URL": BASE, "OMEGA_API_KEY": "__TOKEN__", "OMEGA_TIMEOUT_SECONDS": "mucho"}, "segundos"),
    ],
)
def test_configuration_is_strict(env, fragment):
    token = _token()
    resolved = {key: (token if value == "__TOKEN__" else value) for key, value in env.items()}
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.load_config(resolved)
    assert fragment in str(exc.value)
    assert token not in str(exc.value)


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000", BASE + "/"])
def test_https_or_local_http_is_accepted(base):
    token = _token()
    config = bridge.load_config({"OMEGA_BASE_URL": base, "OMEGA_API_KEY": token, "OMEGA_SOLO_LECTURA": "1"})
    assert config.base_url == base.rstrip("/")
    assert config.read_only is True
    assert config.timeout_seconds == 300.0
    assert token not in repr(config) and token not in str(config)


def test_token_file_must_be_private(tmp_path):
    token = _token()
    path = tmp_path / "token"
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.load_config({"OMEGA_BASE_URL": BASE, "OMEGA_API_KEY_FILE": str(path)})
    assert "600" in str(exc.value)
    path.chmod(0o600)
    assert bridge.load_config({"OMEGA_BASE_URL": BASE, "OMEGA_API_KEY_FILE": str(path)}).token == token


@pytest.mark.asyncio
async def test_redirects_are_never_followed():
    token = _token()
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://otro.example.test/robar"})

    client = bridge.GatewayClient(_config(token), transport=httpx.MockTransport(handler))
    with pytest.raises(bridge.BridgeError) as exc:
        await client.whoami()
    await client.aclose()
    assert str(exc.value) == bridge.MSG_REDIRECT
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_unauthorized_and_timeouts_have_spanish_messages():
    token = _token()
    client = bridge.GatewayClient(_config(token), transport=_gateway(token, [], status=401))
    with pytest.raises(bridge.BridgeError) as exc:
        await client.whoami()
    assert str(exc.value) == bridge.MSG_UNAUTHORIZED
    await client.aclose()

    def slow(request):
        raise httpx.ReadTimeout("lento", request=request)

    client = bridge.GatewayClient(_config(token), transport=httpx.MockTransport(slow))
    with pytest.raises(bridge.BridgeError) as exc:
        await client.execute("ejecutar_extraccion", {"fuente": "sap b1"})
    assert "podría seguir en curso" in str(exc.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_leading_keepalive_whitespace_is_tolerated():
    token = _token()
    body = "\n\n\n" + json.dumps({"ok": True, "accion": "ejecutar_extraccion", "resumen": "x", "datos": {"ejecucion_id": "r1"}, "truncado": False})
    client = bridge.GatewayClient(_config(token), transport=_gateway(token, [], execute_body=body))
    envelope = await client.execute("ejecutar_extraccion", {"fuente": "sap b1"})
    assert envelope["datos"] == {"ejecucion_id": "r1"}
    await client.aclose()


@pytest.mark.asyncio
async def test_sdk_session_lists_and_calls_tools_through_the_gateway():
    token = _token()
    requests: list[httpx.Request] = []
    config = _config(token)
    client = bridge.GatewayClient(config, transport=_gateway(token, requests))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == ["consultar_contexto", "ejecutar_extraccion"]
        assert tools.tools[0].annotations.readOnlyHint is True
        result = await session.call_tool("ejecutar_extraccion", {"fuente": "sap b1"})
        assert result.isError is False
        assert result.structuredContent == {"eco": {"fuente": "sap b1"}}
    posted = [json.loads(request.content) for request in requests if request.method == "POST"]
    assert posted == [{"accion": "ejecutar_extraccion", "argumentos": {"fuente": "sap b1"}}]
    await client.aclose()


@pytest.mark.asyncio
async def test_read_only_mode_hides_and_refuses_mutating_tools():
    token = _token()
    requests: list[httpx.Request] = []
    config = _config(token, read_only=True)
    client = bridge.GatewayClient(config, transport=_gateway(token, requests))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == ["consultar_contexto"]
        refused = await session.call_tool("ejecutar_extraccion", {"fuente": "sap b1"})
        assert refused.isError is True
        assert refused.content[0].text == bridge.MSG_READ_ONLY
    assert not [request for request in requests if request.method == "POST"]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalog", "name"),
    [
        (ACTIONS, "crear_app_analitica"),
        (ACTIONS, "herramienta_nueva"),
        ([{**ACTIONS[0], "alcance": None}], "consultar_contexto"),
        ([{key: value for key, value in ACTIONS[0].items() if key != "alcance"}], "consultar_contexto"),
    ],
)
async def test_read_only_mode_fails_closed_for_tools_outside_the_read_catalog(catalog, name, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "ACTIONS", catalog)
    token = _token()
    requests: list[httpx.Request] = []
    config = _config(token, read_only=True)
    client = bridge.GatewayClient(config, transport=_gateway(token, requests))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        refused = await session.call_tool(name, {})
        assert refused.isError is True
        assert refused.content[0].text == bridge.MSG_READ_ONLY
    assert not [request for request in requests if request.method == "POST"]
    listings = [request for request in requests if request.url.path == "/api/ia/v1/actions"]
    assert 1 <= len(listings) <= 2
    await client.aclose()


@pytest.mark.asyncio
async def test_read_only_mode_still_runs_read_tools():
    token = _token()
    requests: list[httpx.Request] = []
    config = _config(token, read_only=True)
    client = bridge.GatewayClient(config, transport=_gateway(token, requests))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("consultar_contexto", {})
        assert result.isError is False
    posted = [json.loads(request.content) for request in requests if request.method == "POST"]
    assert posted == [{"accion": "consultar_contexto", "argumentos": {}}]
    await client.aclose()


@pytest.mark.asyncio
async def test_read_only_mode_refuses_when_the_catalog_is_unavailable():
    token = _token()
    requests: list[httpx.Request] = []
    config = _config(token, read_only=True)
    client = bridge.GatewayClient(config, transport=_gateway(token, requests, status=503))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        refused = await session.call_tool("consultar_contexto", {})
        assert refused.isError is True
    assert not [request for request in requests if request.method == "POST"]
    await client.aclose()


@pytest.mark.asyncio
async def test_server_errors_surface_spanish_server_messages():
    token = _token()
    error = json.dumps({"ok": False, "accion": "ejecutar_extraccion", "error": {"codigo": "alcance_insuficiente", "mensaje": "Este token es de solo lectura.", "reintentable": False}})
    config = _config(token)
    client = bridge.GatewayClient(config, transport=_gateway(token, [], execute_body=error))
    server = bridge.build_server(client, config)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("ejecutar_extraccion", {"fuente": "sap b1"})
        assert result.isError is True
        assert "Este token es de solo lectura." in result.content[0].text
        assert "Input validation error" not in result.content[0].text
    await client.aclose()


def test_check_mode_never_prints_the_token(monkeypatch, capsys):
    token = _token()
    original = bridge.GatewayClient

    def with_transport(config):
        return original(config, transport=_gateway(token, []))

    monkeypatch.setattr(bridge, "GatewayClient", with_transport)
    monkeypatch.setenv("OMEGA_BASE_URL", BASE)
    monkeypatch.setenv("OMEGA_API_KEY", token)
    monkeypatch.delenv("OMEGA_API_KEY_FILE", raising=False)
    assert bridge.main(["--check"]) == 0
    captured = capsys.readouterr()
    assert "Conexión correcta con OMEGA" in captured.err
    assert "Operaciones" in captured.err
    assert token not in captured.err and token not in captured.out
    assert captured.out == ""


def test_check_mode_reports_errors_without_the_token(monkeypatch, capsys):
    token = _token()
    original = bridge.GatewayClient

    def with_transport(config):
        return original(config, transport=_gateway(token, [], status=401))

    monkeypatch.setattr(bridge, "GatewayClient", with_transport)
    monkeypatch.setenv("OMEGA_BASE_URL", BASE)
    monkeypatch.setenv("OMEGA_API_KEY", token)
    assert bridge.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert bridge.MSG_UNAUTHORIZED in captured.err
    assert token not in captured.err + captured.out


def test_configuration_errors_exit_2(monkeypatch, capsys):
    monkeypatch.setenv("OMEGA_BASE_URL", "http://consola.example.test")
    monkeypatch.setenv("OMEGA_API_KEY", _token())
    assert bridge.main(["--check"]) == 2
    assert "https" in capsys.readouterr().err


def test_help_is_spanish(capsys):
    with pytest.raises(SystemExit) as exc:
        bridge.main(["--help"])
    assert exc.value.code == 0
    assert "Puente local" in capsys.readouterr().out


def test_log_redaction_masks_tokens(capsys):
    token = _token()
    bridge.configure_logging(token)
    logging.getLogger("omega_mcp_bridge").warning("fallo con %s", token)
    logging.getLogger("otro").warning("encabezado Bearer %s", _token())
    err = capsys.readouterr().err
    assert token not in err
    assert "omega_pat_****" in err
    assert logging.getLogger("httpx").level == logging.WARNING

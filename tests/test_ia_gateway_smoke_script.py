from __future__ import annotations

import importlib.util
import json
import secrets
import string
import sys
from pathlib import Path

import httpx
import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ia_gateway_smoke.py"
BASE = "https://consola.example.test"
WORKSPACE = "11111111-1111-1111-1111-111111111111"


def _load():
    spec = importlib.util.spec_from_file_location("ia_gateway_smoke_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()


def _token() -> str:
    return "omega_pat_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(43))


def _ok(accion: str, datos: dict) -> dict:
    return {
        "ok": True,
        "accion": accion,
        "espacio_de_trabajo": {"id": WORKSPACE, "nombre": "Operaciones"},
        "resumen": "listo",
        "datos": datos,
        "truncado": False,
    }


def _fake_gateway(token: str, *, scopes: list[str], leak_outside: bool = False, polls: list[str] | None = None):
    states = list(polls or ["running", "success"])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        authorized = request.headers.get("authorization") == f"Bearer {token}"
        if path == "/api/ia/v1/openapi.json":
            return httpx.Response(200, json={"openapi": "3.1.0"})
        if path == "/api/datasets":
            return httpx.Response(200 if leak_outside else 401, json={"detail": "x"})
        if not authorized:
            return httpx.Response(401, json={"ok": False, "error": {"codigo": "token_invalido"}})
        if path == "/api/ia/v1/whoami":
            return httpx.Response(200, json=_ok("consultar_identidad", {"alcances": scopes, "fuentes_habilitadas": ["SAP Business One"]}))
        if path == "/api/ia/v1/actions":
            names = ["consultar_contexto", "listar_tablas_disponibles"] + (["ejecutar_extraccion"] if "acciones" in scopes else [])
            return httpx.Response(200, json=_ok("listar_acciones", {"acciones": [{"nombre": name} for name in names]}))
        if path == "/api/ia/v1/actions/ejecutar_extraccion":
            if "acciones" not in scopes:
                return httpx.Response(403, json={"ok": False, "error": {"codigo": "alcance_insuficiente"}})
            return httpx.Response(200, text="\n" + json.dumps(_ok("ejecutar_extraccion", {"ejecucion_id": "r1"})))
        if path == "/api/ia/v1/actions/consultar_extraccion":
            state = states.pop(0) if states else "success"
            return httpx.Response(200, json=_ok("consultar_extraccion", {"estado": state, "estado_texto": state, "avance_pct": 50}))
        if path.startswith("/api/ia/v1/actions/"):
            return httpx.Response(200, json=_ok(path.rsplit("/", 1)[-1], {}))
        if path == "/api/ia/v1/mcp":
            if request.method == "GET":
                return httpx.Response(405, headers={"allow": "POST"})
            message = json.loads(request.content)
            results = {"initialize": {"protocolVersion": "2025-11-25"}, "tools/list": {"tools": [{"name": "consultar_contexto"}]}, "ping": {}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": results[message["method"]]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_read_only_token_passes_every_check():
    token = _token()
    report = await smoke.run_checks(
        smoke.SmokeConfig(base_url=BASE, token=token), transport=_fake_gateway(token, scopes=["lectura"])
    )
    text = "\n".join(report.lines)
    assert report.failures == 0, text
    for name in (
        "identidad",
        "catálogo",
        "esquema público sin token",
        "consultar_contexto",
        "token rechazado fuera de la pasarela",
        "espacio ajeno ignorado",
        "token de solo lectura no ejecuta acciones",
        "GET del conector remoto responde 405",
        "conector remoto: initialize",
        "conector remoto: tools/list",
        "conector remoto: ping",
    ):
        assert f"PASS {name}" in text
    assert "OMITIDO consultar_kpis_sap_b1" in text
    assert token not in text


@pytest.mark.asyncio
async def test_leaking_token_outside_the_gateway_fails():
    token = _token()
    report = await smoke.run_checks(
        smoke.SmokeConfig(base_url=BASE, token=token),
        transport=_fake_gateway(token, scopes=["lectura"], leak_outside=True),
    )
    assert report.failures == 1
    assert any(line.startswith("FAIL token rechazado fuera de la pasarela") for line in report.lines)


@pytest.mark.asyncio
async def test_actions_mode_starts_and_polls_an_extraction():
    token = _token()
    report = await smoke.run_checks(
        smoke.SmokeConfig(base_url=BASE, token=token),
        include_actions=True,
        poll_seconds=0,
        transport=_fake_gateway(token, scopes=["acciones", "lectura"], polls=["queued", "running", "success"]),
    )
    text = "\n".join(report.lines)
    assert report.failures == 0, text
    assert "PASS extracción incremental iniciada" in text
    assert "PASS extracción incremental terminada" in text
    assert "OMITIDO token de solo lectura no ejecuta acciones" in text


def test_main_prints_results_without_the_token(monkeypatch, tmp_path, capsys):
    token = _token()
    token_file = tmp_path / "token"
    token_file.write_text(token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("OMEGA_BASE_URL", BASE)
    monkeypatch.setenv("OMEGA_API_KEY_FILE", str(token_file))
    original = smoke.run_checks

    async def with_transport(config, **kwargs):
        return await original(config, transport=_fake_gateway(token, scopes=["lectura"]), **kwargs)

    monkeypatch.setattr(smoke, "run_checks", with_transport)
    assert smoke.main([]) == 0
    captured = capsys.readouterr()
    assert "Resultado: CORRECTO" in captured.out
    assert token not in captured.out + captured.err


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"OMEGA_BASE_URL": "http://consola.example.test"}, "https"),
        ({"OMEGA_BASE_URL": BASE}, "OMEGA_API_KEY_FILE"),
    ],
)
def test_configuration_errors(monkeypatch, capsys, env, fragment):
    monkeypatch.delenv("OMEGA_API_KEY_FILE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert smoke.main([]) == 2
    assert fragment in capsys.readouterr().err


def test_world_readable_token_file_is_refused(monkeypatch, tmp_path, capsys):
    token_file = tmp_path / "token"
    token_file.write_text(_token(), encoding="utf-8")
    token_file.chmod(0o644)
    monkeypatch.setenv("OMEGA_BASE_URL", BASE)
    monkeypatch.setenv("OMEGA_API_KEY_FILE", str(token_file))
    assert smoke.main([]) == 2
    assert "600" in capsys.readouterr().err

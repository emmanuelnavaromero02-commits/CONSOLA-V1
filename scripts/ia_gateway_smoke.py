#!/usr/bin/env python3
"""Owner-run smoke checks for the AI gateway; prints PASS/FAIL and never the token."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


TOKEN_RE = re.compile(r"omega_pat_[A-Za-z0-9]{43}")
GATEWAY_PATH = "/api/ia/v1"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
READ_ACTION_ARGS: dict[str, dict[str, Any]] = {
    "consultar_contexto": {},
    "consultar_salud_pipelines": {},
    "consultar_control_room_resumen": {},
    "consultar_matriz_talento_9box": {"collar": "confianza"},
    "consultar_kpis_sap_b1": {"area": "semaforo", "top_n": 3},
    "buscar_documentos_empresa": {"consulta": "política", "max_resultados": 3},
    "listar_tablas_disponibles": {"capa": "gold"},
}
TERMINAL_STATUSES = frozenset({"success", "partial", "failed", "blocked", "skipped", "skipped_explicit"})


class SmokeConfigError(Exception):
    """Invalid smoke configuration."""


@dataclass(frozen=True)
class SmokeConfig:
    base_url: str
    token: str = field(repr=False)


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failures: int = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        suffix = f": {detail}" if detail else ""
        self.lines.append(f"{'PASS' if ok else 'FAIL'} {name}{suffix}")
        if not ok:
            self.failures += 1
        return ok

    def skip(self, name: str, detail: str) -> None:
        self.lines.append(f"OMITIDO {name}: {detail}")


def load_config(environ: Mapping[str, str] | None = None) -> SmokeConfig:
    env = os.environ if environ is None else environ
    base_url = (env.get("OMEGA_BASE_URL") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise SmokeConfigError("Configura OMEGA_BASE_URL con la dirección completa de la consola.")
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise SmokeConfigError("OMEGA_BASE_URL debe usar https; solo localhost puede usar http.")
    token_file = (env.get("OMEGA_API_KEY_FILE") or "").strip()
    if not token_file:
        raise SmokeConfigError("Configura OMEGA_API_KEY_FILE con la ruta del archivo del token.")
    path = Path(token_file).expanduser()
    try:
        info = path.stat()
    except OSError as exc:
        raise SmokeConfigError("No se encontró el archivo del token.") from exc
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SmokeConfigError("El archivo del token no debe ser legible por otros usuarios; usa permisos 600.")
    token = path.read_text(encoding="utf-8").strip()
    if not TOKEN_RE.fullmatch(token):
        raise SmokeConfigError("El archivo no contiene un token personal válido.")
    return SmokeConfig(base_url=base_url, token=token)


def _envelope(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = json.loads(response.text.strip() or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _code(envelope: Mapping[str, Any]) -> str:
    error = envelope.get("error")
    return str(error.get("codigo")) if isinstance(error, Mapping) else ""


async def run_checks(
    config: SmokeConfig,
    *,
    include_actions: bool = False,
    source: str | None = None,
    poll_seconds: float = 15.0,
    max_polls: int = 20,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Report:
    report = Report()
    auth = {"Authorization": f"Bearer {config.token}"}
    async with httpx.AsyncClient(
        base_url=config.base_url,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
        transport=transport,
    ) as client:
        whoami = await client.get(f"{GATEWAY_PATH}/whoami", headers=auth)
        identity = _envelope(whoami)
        report.check("identidad", whoami.status_code == 200 and identity.get("ok") is True, f"HTTP {whoami.status_code}")
        workspace = (identity.get("espacio_de_trabajo") or {}).get("id")
        scopes = set((identity.get("datos") or {}).get("alcances") or [])

        catalog_response = await client.get(f"{GATEWAY_PATH}/actions", headers=auth)
        catalog = _envelope(catalog_response)
        actions = {
            item.get("nombre"): item
            for item in ((catalog.get("datos") or {}).get("acciones") or [])
            if isinstance(item, Mapping)
        }
        report.check("catálogo", catalog_response.status_code == 200 and bool(actions), f"{len(actions)} acciones")

        spec = await client.get(f"{GATEWAY_PATH}/openapi.json")
        report.check(
            "esquema público sin token",
            spec.status_code == 200 and _envelope(spec).get("openapi") == "3.1.0",
            f"HTTP {spec.status_code}",
        )

        for name, arguments in READ_ACTION_ARGS.items():
            if name not in actions:
                report.skip(name, "no disponible para este token")
                continue
            response = await client.post(f"{GATEWAY_PATH}/actions/{name}", json=arguments, headers=auth)
            envelope = _envelope(response)
            report.check(name, response.status_code == 200 and envelope.get("ok") is True, f"HTTP {response.status_code} {_code(envelope)}".strip())

        outside = await client.get("/api/datasets", headers=auth)
        report.check("token rechazado fuera de la pasarela", outside.status_code == 401, f"HTTP {outside.status_code}")

        foreign = await client.get(
            f"{GATEWAY_PATH}/whoami", headers={**auth, "X-Workspace-Id": str(uuid.uuid4())}
        )
        report.check(
            "espacio ajeno ignorado",
            foreign.status_code == 200 and (_envelope(foreign).get("espacio_de_trabajo") or {}).get("id") == workspace,
            f"HTTP {foreign.status_code}",
        )

        if "acciones" in scopes:
            report.skip("token de solo lectura no ejecuta acciones", "el token tiene alcance de acciones")
        else:
            denied = await client.post(
                f"{GATEWAY_PATH}/actions/ejecutar_extraccion", json={"fuente": "SAP Business One"}, headers=auth
            )
            report.check(
                "token de solo lectura no ejecuta acciones",
                denied.status_code == 403 and _code(_envelope(denied)) == "alcance_insuficiente",
                f"HTTP {denied.status_code}",
            )

        stream = await client.get(f"{GATEWAY_PATH}/mcp", headers=auth)
        report.check("GET del conector remoto responde 405", stream.status_code == 405, f"HTTP {stream.status_code}")

        rpc_headers = {**auth, "Accept": "application/json, text/event-stream"}
        initialize = await client.post(
            f"{GATEWAY_PATH}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
            },
            headers=rpc_headers,
        )
        report.check(
            "conector remoto: initialize",
            initialize.status_code == 200 and "result" in _envelope(initialize),
            f"HTTP {initialize.status_code}",
        )
        tools = await client.post(
            f"{GATEWAY_PATH}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=rpc_headers
        )
        listed = ((_envelope(tools).get("result") or {}).get("tools")) or []
        report.check("conector remoto: tools/list", tools.status_code == 200 and bool(listed), f"{len(listed)} herramientas")
        ping = await client.post(
            f"{GATEWAY_PATH}/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"}, headers=rpc_headers
        )
        report.check("conector remoto: ping", ping.status_code == 200 and _envelope(ping).get("result") == {}, f"HTTP {ping.status_code}")

        if include_actions:
            await _run_extraction(client, auth, report, actions, identity, source, poll_seconds, max_polls)
    return report


async def _run_extraction(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    report: Report,
    actions: Mapping[str, Any],
    identity: Mapping[str, Any],
    source: str | None,
    poll_seconds: float,
    max_polls: int,
) -> None:
    if "ejecutar_extraccion" not in actions:
        report.check("extracción incremental", False, "el token no tiene alcance de acciones o permiso")
        return
    sources = (identity.get("datos") or {}).get("fuentes_habilitadas") or []
    chosen = source or (sources[0] if sources else "")
    if not chosen:
        report.check("extracción incremental", False, "no hay fuentes habilitadas")
        return
    key = f"smoke-{int(time.time())}"
    started = await client.post(
        f"{GATEWAY_PATH}/actions/ejecutar_extraccion",
        json={"fuente": chosen, "clave_idempotencia": key},
        headers=auth,
    )
    envelope = _envelope(started)
    run_id = (envelope.get("datos") or {}).get("ejecucion_id")
    if not report.check("extracción incremental iniciada", envelope.get("ok") is True and bool(run_id), _code(envelope)):
        return
    state = ""
    for _ in range(max_polls):
        polled = await client.post(
            f"{GATEWAY_PATH}/actions/consultar_extraccion",
            json={"fuente": chosen, "ejecucion_id": run_id},
            headers=auth,
        )
        datos = _envelope(polled).get("datos") or {}
        state = str(datos.get("estado") or "")
        report.lines.append(f"  estado: {datos.get('estado_texto') or 'Sin información'} ({datos.get('avance_pct') or 0}%)")
        if state in TERMINAL_STATUSES:
            break
        await asyncio.sleep(poll_seconds)
    report.check("extracción incremental terminada", state in TERMINAL_STATUSES, state or "sin estado")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ia_gateway_smoke",
        description="Verificación de la pasarela de IA. Usa OMEGA_BASE_URL y OMEGA_API_KEY_FILE.",
    )
    parser.add_argument("--incluir-acciones", action="store_true", help="Ejecuta una extracción incremental y consulta su avance.")
    parser.add_argument("--fuente", default=None, help="Fuente para la extracción (por omisión la primera habilitada).")
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except SmokeConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_checks(config, include_actions=args.incluir_acciones, source=args.fuente))
    except httpx.HTTPError:
        print("FAIL conexión: no se pudo conectar con la consola.", file=sys.stderr)
        return 1
    for line in report.lines:
        print(TOKEN_RE.sub("omega_pat_****", line))
    print(f"Resultado: {'FALLÓ' if report.failures else 'CORRECTO'} ({report.failures} fallas)")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

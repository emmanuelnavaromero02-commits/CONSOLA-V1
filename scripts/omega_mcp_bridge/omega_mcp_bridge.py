#!/usr/bin/env python3
"""Local STDIO bridge that exposes the OMEGA AI gateway to desktop assistants."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import stat
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


TOKEN_RE = re.compile(r"omega_pat_[A-Za-z0-9]{43}")
GATEWAY_PATH = "/api/ia/v1"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
DEFAULT_TIMEOUT_SECONDS = 300.0
MIN_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 600.0
CATALOG_TTL_SECONDS = 300.0
TRUE_VALUES = frozenset({"1", "true", "si", "sí", "yes"})
ANNOTATION_KEYS = ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
MSG_TIMEOUT = (
    "La operación tardó demasiado; la operación podría seguir en curso; consulta su "
    "estado antes de reintentar."
)
MSG_UNAUTHORIZED = "Token inválido, vencido o revocado; genera uno nuevo en Mi acceso."
MSG_REDIRECT = "El servidor respondió con una redirección; revisa OMEGA_BASE_URL."
MSG_CONNECT = "No se pudo conectar con OMEGA; revisa OMEGA_BASE_URL y tu conexión."
MSG_UNEXPECTED = "Respuesta inesperada del servidor de OMEGA."
MSG_READ_ONLY = (
    "Este puente está en modo solo lectura (OMEGA_SOLO_LECTURA=1); solo se ejecutan "
    "herramientas de lectura del catálogo y la acción no se ejecutó."
)

logger = logging.getLogger("omega_mcp_bridge")


class BridgeError(Exception):
    """A Spanish, user-facing bridge failure."""


@dataclass(frozen=True)
class BridgeConfig:
    base_url: str
    token: str
    timeout_seconds: float
    read_only: bool

    def __repr__(self) -> str:
        return (
            f"BridgeConfig(base_url={self.base_url!r}, token='omega_pat_****', "
            f"timeout_seconds={self.timeout_seconds}, read_only={self.read_only})"
        )

    __str__ = __repr__


class RedactionFilter(logging.Filter):
    def __init__(self, secret: str | None = None) -> None:
        super().__init__()
        self._secret = secret

    def _clean(self, text: str) -> str:
        cleaned = TOKEN_RE.sub("omega_pat_****", text)
        if self._secret:
            cleaned = cleaned.replace(self._secret, "omega_pat_****")
        return cleaned

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._clean(str(record.getMessage()))
        record.args = None
        return True


def configure_logging(secret: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactionFilter(secret))
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _read_token_file(raw_path: str) -> str:
    path = Path(raw_path).expanduser()
    try:
        info = path.stat()
    except OSError as exc:
        raise BridgeError("No se encontró el archivo indicado en OMEGA_API_KEY_FILE.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BridgeError("OMEGA_API_KEY_FILE debe apuntar a un archivo.")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise BridgeError(
            "El archivo del token no debe ser legible por otros usuarios; usa permisos 600."
        )
    return path.read_text(encoding="utf-8").strip()


def load_config(environ: Mapping[str, str] | None = None) -> BridgeConfig:
    env = os.environ if environ is None else environ
    base_url = (env.get("OMEGA_BASE_URL") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise BridgeError("Configura OMEGA_BASE_URL con la dirección completa de la consola.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BridgeError("OMEGA_BASE_URL no debe incluir usuario, contraseña ni parámetros.")
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise BridgeError("OMEGA_BASE_URL debe usar https; solo localhost puede usar http.")
    if parsed.path.rstrip("/"):
        raise BridgeError("OMEGA_BASE_URL debe ser la dirección raíz de la consola, sin ruta.")
    token_file = (env.get("OMEGA_API_KEY_FILE") or "").strip()
    token = _read_token_file(token_file) if token_file else (env.get("OMEGA_API_KEY") or "").strip()
    if not token:
        raise BridgeError("Configura OMEGA_API_KEY_FILE (recomendado) u OMEGA_API_KEY con tu token personal.")
    if not TOKEN_RE.fullmatch(token):
        raise BridgeError("El token no tiene el formato de un token personal de OMEGA.")
    raw_timeout = (env.get("OMEGA_TIMEOUT_SECONDS") or "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError as exc:
        raise BridgeError("OMEGA_TIMEOUT_SECONDS debe ser un número de segundos.") from exc
    if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise BridgeError("OMEGA_TIMEOUT_SECONDS debe estar entre 10 y 600.")
    read_only = (env.get("OMEGA_SOLO_LECTURA") or "").strip().lower() in TRUE_VALUES
    return BridgeConfig(base_url=base_url, token=token, timeout_seconds=timeout, read_only=read_only)


def parse_envelope(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text.strip())
    except ValueError as exc:
        raise BridgeError(MSG_UNEXPECTED) from exc
    if not isinstance(payload, dict) or "ok" not in payload:
        raise BridgeError(MSG_UNEXPECTED)
    return payload


def _error_message(envelope: Mapping[str, Any]) -> str:
    error = envelope.get("error") if isinstance(envelope.get("error"), Mapping) else {}
    message = error.get("mensaje") if isinstance(error, Mapping) else None
    return str(message) if isinstance(message, str) and message else MSG_UNEXPECTED


class GatewayClient:
    def __init__(self, config: BridgeConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url + GATEWAY_PATH,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/json",
                "User-Agent": "omega-mcp-bridge",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
            transport=transport,
        )
        self._catalog: list[dict[str, Any]] | None = None
        self._catalog_at = 0.0

    def __repr__(self) -> str:
        return f"GatewayClient(base_url={self._config.base_url!r})"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, body: Any = None) -> dict[str, Any]:
        try:
            response = await asyncio.wait_for(
                self._client.request(method, path, json=body),
                timeout=self._config.timeout_seconds,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise BridgeError(MSG_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise BridgeError(MSG_CONNECT) from exc
        if response.is_redirect or 300 <= response.status_code < 400:
            raise BridgeError(MSG_REDIRECT)
        try:
            envelope = parse_envelope(response.text)
        except BridgeError:
            if response.status_code == 401:
                raise BridgeError(MSG_UNAUTHORIZED) from None
            raise
        if response.status_code == 401:
            raise BridgeError(MSG_UNAUTHORIZED)
        return envelope

    async def whoami(self) -> dict[str, Any]:
        envelope = await self._request("GET", "/whoami")
        if not envelope.get("ok"):
            raise BridgeError(_error_message(envelope))
        return envelope

    async def actions(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        fresh = self._catalog is not None and time.monotonic() - self._catalog_at < CATALOG_TTL_SECONDS
        if fresh and not refresh:
            return list(self._catalog or [])
        envelope = await self._request("GET", "/actions")
        if not envelope.get("ok"):
            raise BridgeError(_error_message(envelope))
        datos = envelope.get("datos") if isinstance(envelope.get("datos"), Mapping) else {}
        actions = [item for item in datos.get("acciones") or [] if isinstance(item, Mapping)]
        self._catalog = [dict(item) for item in actions]
        self._catalog_at = time.monotonic()
        return list(self._catalog)

    async def execute(self, name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        return await self._request(
            "POST", "/actions/execute", body={"accion": name, "argumentos": dict(arguments or {})}
        )


def _tool(action: Mapping[str, Any]) -> types.Tool:
    annotations = action.get("anotaciones") if isinstance(action.get("anotaciones"), Mapping) else {}
    schema = action.get("esquema_de_entrada") if isinstance(action.get("esquema_de_entrada"), Mapping) else {}
    return types.Tool(
        name=str(action.get("nombre")),
        title=action.get("titulo"),
        description=action.get("descripcion"),
        inputSchema=dict(schema) or {"type": "object", "properties": {}},
        annotations=types.ToolAnnotations(
            **{key: annotations[key] for key in ANNOTATION_KEYS if key in annotations}
        ),
    )


def _text_result(text: str, *, is_error: bool, structured: dict[str, Any] | None = None) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=is_error,
    )


def build_server(client: GatewayClient, config: BridgeConfig) -> Server:
    server: Server = Server("omega")

    async def _visible() -> list[dict[str, Any]]:
        actions = await client.actions()
        if config.read_only:
            actions = [action for action in actions if action.get("alcance") == "lectura"]
        return actions

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [_tool(action) for action in await _visible() if action.get("nombre")]

    async def _read_only_allows(name: str) -> bool:
        known = {str(action.get("nombre")): action for action in await client.actions()}
        if name not in known:
            known = {
                str(action.get("nombre")): action
                for action in await client.actions(refresh=True)
            }
        return name in known and known[name].get("alcance") == "lectura"

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        try:
            if config.read_only and not await _read_only_allows(name):
                return _text_result(MSG_READ_ONLY, is_error=True)
            envelope = await client.execute(name, arguments)
        except BridgeError as exc:
            return _text_result(str(exc), is_error=True)
        text = json.dumps(envelope, ensure_ascii=False)
        if envelope.get("ok"):
            datos = envelope.get("datos")
            return _text_result(text, is_error=False, structured=datos if isinstance(datos, dict) else None)
        return _text_result(text, is_error=True)

    return server


async def run_check(client: GatewayClient) -> int:
    try:
        envelope = await client.whoami()
        actions = await client.actions(refresh=True)
    except BridgeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    datos = envelope.get("datos") if isinstance(envelope.get("datos"), Mapping) else {}
    workspace = envelope.get("espacio_de_trabajo") if isinstance(envelope.get("espacio_de_trabajo"), Mapping) else {}
    token = datos.get("token") if isinstance(datos.get("token"), Mapping) else {}
    print(
        "Conexión correcta con OMEGA. "
        f"Espacio de trabajo: {workspace.get('nombre') or 'Sin información'}. "
        f"Alcance: {', '.join(datos.get('alcances') or []) or 'Sin información'}. "
        f"Vence: {token.get('vence_en') or 'Sin información'}. "
        f"Acciones disponibles: {len(actions)}.",
        file=sys.stderr,
    )
    return 0


async def run_stdio(client: GatewayClient, config: BridgeConfig) -> None:
    server = build_server(client, config)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omega_mcp_bridge",
        description=(
            "Puente local que conecta asistentes de escritorio con la pasarela de IA de OMEGA. "
            "Variables: OMEGA_BASE_URL, OMEGA_API_KEY_FILE u OMEGA_API_KEY, "
            "OMEGA_TIMEOUT_SECONDS (10-600) y OMEGA_SOLO_LECTURA=1."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifica la configuración y la conexión, imprime un diagnóstico y termina.",
    )
    return parser


async def _main_async(args: argparse.Namespace, config: BridgeConfig) -> int:
    client = GatewayClient(config)
    try:
        if args.check:
            return await run_check(client)
        await run_stdio(client, config)
        return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    try:
        config = load_config()
    except BridgeError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    configure_logging(config.token)
    return asyncio.run(_main_async(args, config))


if __name__ == "__main__":
    raise SystemExit(main())

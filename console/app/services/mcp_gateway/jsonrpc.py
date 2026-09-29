from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.domains.security.cors import allowed_origins
from app.services.mcp_gateway import catalog, dispatcher
from app.services.mcp_gateway.errors import gateway_error_from_exception, error_body
from app.version import app_version


SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_PROTOCOL_VERSION = "2025-11-25"
MAX_BODY_BYTES = 32_768
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
SERVER_INFO = {"name": "omega", "title": "ΩMEGA", "version": ""}
INSTRUCTIONS = (
    "Herramientas de ΩMEGA para consultar el espacio de trabajo ligado a tu token personal. "
    "Empieza con consultar_contexto, responde en español y cita el resumen de cada resultado."
)
_JSON_HEADERS = {"Cache-Control": "no-store"}
_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _error(code: int, message: str, request_id: Any = None, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status_code,
        headers=_JSON_HEADERS,
    )


def _result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result}, headers=_JSON_HEADERS
    )


def origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return True
    try:
        allowed = allowed_origins(os.environ)
    except RuntimeError:
        return False
    return origin.strip().rstrip("/") in {item.rstrip("/") for item in allowed}


async def read_body_capped(request: Request, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if declared > max_bytes:
            raise HTTPException(413, "Request body exceeds size limit")
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(413, "Request body exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def tool_definitions(user: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": action.name,
            "title": action.title,
            "description": action.description,
            "inputSchema": action.schema_copy(),
            "annotations": action.mcp_annotations,
        }
        for action in catalog.visible_actions(user)
    ]


def call_result(envelope: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(envelope, ensure_ascii=False)
    if envelope.get("ok"):
        datos = envelope.get("datos")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": datos if isinstance(datos, dict) else {"valor": datos},
            "isError": False,
        }
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {**SERVER_INFO, "version": app_version()},
        "instructions": INSTRUCTIONS,
    }


def _wants_sse(request: Request) -> bool:
    return "text/event-stream" in (request.headers.get("accept") or "").lower()


def _sse_bytes(message: dict[str, Any]) -> bytes:
    return f"event: message\ndata: {json.dumps(message, ensure_ascii=False)}\n\n".encode("utf-8")


async def _tools_call(request: Request, request_id: Any, params: dict[str, Any], user: dict[str, Any]) -> Response:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or catalog.get_action(name) is None:
        return _error(INVALID_PARAMS, "Herramienta desconocida.", request_id)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _error(INVALID_PARAMS, "Los argumentos deben ser un objeto JSON.", request_id)
    try:
        prepared = await dispatcher.prepare(name, arguments, user)
    except Exception as exc:
        envelope = error_body(gateway_error_from_exception(exc), accion=name)
        return _result(request_id, call_result(envelope))
    if prepared.action.long_running and _wants_sse(request):

        def render(envelope: dict[str, Any]) -> bytes:
            return _sse_bytes({"jsonrpc": "2.0", "id": request_id, "result": call_result(envelope)})

        return StreamingResponse(
            dispatcher.keepalive_stream(
                lambda: dispatcher.run_envelope(prepared, user),
                heartbeat=b": keep-alive\n\n",
                render=render,
            ),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    _status, envelope = await dispatcher.run_envelope(prepared, user)
    return _result(request_id, call_result(envelope))


async def handle(request: Request, user: dict[str, Any]) -> Response:
    if not origin_allowed(request.headers.get("origin")):
        return _error(INVALID_REQUEST, "Origen no permitido.", status_code=403)
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _error(INVALID_REQUEST, "Versión de protocolo no soportada.", status_code=400)
    try:
        raw = await read_body_capped(request)
    except HTTPException as exc:
        return _error(INVALID_REQUEST, "La solicitud excede el tamaño permitido o es inválida.", status_code=exc.status_code)
    try:
        message = json.loads(raw or b"")
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _error(PARSE_ERROR, "El cuerpo no es JSON válido.", status_code=400)
    if isinstance(message, list):
        return _error(INVALID_REQUEST, "No se admiten lotes de mensajes.", status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(INVALID_REQUEST, "Mensaje JSON-RPC inválido.", status_code=400)
    has_id = "id" in message
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        if not has_id or "result" in message or "error" in message:
            return Response(status_code=202)
        return _error(INVALID_REQUEST, "Mensaje JSON-RPC inválido.", status_code=400)
    if not has_id:
        return Response(status_code=202)
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        return _error(INVALID_REQUEST, "Identificador de solicitud inválido.", status_code=400)
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(INVALID_PARAMS, "Los parámetros deben ser un objeto.", request_id)
    if method == "initialize":
        return _result(request_id, _initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions(user)})
    if method == "tools/call":
        return await _tools_call(request, request_id, params, user)
    return _error(METHOD_NOT_FOUND, "Método no encontrado.", request_id)


__all__ = (
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "call_result",
    "handle",
    "origin_allowed",
    "read_body_capped",
    "tool_definitions",
)

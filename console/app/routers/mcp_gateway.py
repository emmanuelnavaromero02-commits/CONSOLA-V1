from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.domains.data_platform.rag_requests import read_json_body_capped
from app.domains.security.access_token_auth import require_gateway_token
from app.services.csrf import require_csrf
from app.services.mcp_gateway import adapters, catalog, dispatcher, jsonrpc, openapi, sources
from app.services.mcp_gateway.errors import (
    GatewayError,
    current_request_id,
    error_response,
    exception_response,
    gateway_error_from_exception,
)


router = APIRouter(tags=["IA gateway"])

MAX_BODY_BYTES = 32_768
_NO_STORE = {"Cache-Control": "no-store"}
_EXECUTE_KEYS = frozenset({"accion", "argumentos"})


def _json_bytes(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def _envelope(accion: str, user: dict[str, Any], resumen: str, datos: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "accion": accion,
        "espacio_de_trabajo": adapters.workspace_summary(user),
        "resumen": resumen,
        "datos": datos,
        "truncado": False,
        "solicitud_id": current_request_id(),
        "generado_en": dispatcher.utc_now(),
    }


async def _read_arguments(request: Request) -> dict[str, Any]:
    return await read_json_body_capped(request, MAX_BODY_BYTES)


async def _respond(action_name: str, arguments: Any, user: dict[str, Any]) -> Response:
    known = catalog.get_action(action_name) is not None
    try:
        prepared = await dispatcher.prepare(action_name, arguments, user)
    except Exception as exc:
        return error_response(
            gateway_error_from_exception(exc), accion=action_name if known else None
        )
    if prepared.action.long_running:
        return StreamingResponse(
            dispatcher.keepalive_stream(
                lambda: dispatcher.run_envelope(prepared, user),
                heartbeat=b"\n",
                render=_json_bytes,
            ),
            media_type="application/json",
            headers=_NO_STORE,
        )
    status, envelope = await dispatcher.run_envelope(prepared, user)
    return JSONResponse(envelope, status_code=status, headers=_NO_STORE)


@router.get("/api/ia/v1/openapi.json")
async def ia_gateway_openapi(request: Request, alcance: str | None = None):
    try:
        url = openapi.server_url(request)
    except GatewayError as exc:
        return error_response(exc)
    return JSONResponse(
        openapi.build_document(url=url, read_only=alcance == catalog.SCOPE_READ),
        headers=_NO_STORE,
    )


@router.get("/api/ia/v1/whoami", dependencies=[Depends(require_gateway_token)])
async def ia_gateway_whoami(user: dict = Depends(require_gateway_token)):
    try:
        checked = adapters.require_gateway_user(user)
        scopes = sorted(catalog.token_scopes(checked))
        datos = {
            "usuario": checked.get("name") or "Sin información",
            "alcances": scopes,
            "token": {
                "prefijo": checked.get("access_token_prefix"),
                "vence_en": checked.get("access_token_expires_at"),
            },
            "fuentes_habilitadas": sources.enabled_source_labels(checked),
        }
        workspace = adapters.workspace_summary(checked)
        return JSONResponse(
            _envelope("consultar_identidad", checked, f"Token ligado a «{workspace['nombre']}».", datos),
            headers=_NO_STORE,
        )
    except Exception as exc:
        return exception_response(exc, accion="consultar_identidad")


@router.get("/api/ia/v1/actions", dependencies=[Depends(require_gateway_token)])
async def ia_gateway_actions(user: dict = Depends(require_gateway_token)):
    try:
        checked = adapters.require_gateway_user(user)
        acciones = [
            {
                "nombre": action.name,
                "titulo": action.title,
                "descripcion": action.description,
                "alcance": action.scope,
                "consecuente": action.consequential,
                "larga_duracion": action.long_running,
                "esquema_de_entrada": action.schema_copy(),
                "anotaciones": action.mcp_annotations,
            }
            for action in catalog.visible_actions(checked)
        ]
        return JSONResponse(
            _envelope(
                "listar_acciones",
                checked,
                f"{len(acciones)} acciones disponibles para este token.",
                {"acciones": acciones},
            ),
            headers=_NO_STORE,
        )
    except Exception as exc:
        return exception_response(exc, accion="listar_acciones")


@router.post(
    "/api/ia/v1/actions/execute",
    dependencies=[Depends(require_gateway_token), Depends(require_csrf)],
)
async def ia_gateway_execute(request: Request, user: dict = Depends(require_gateway_token)):
    try:
        body = await _read_arguments(request)
        if set(body) - _EXECUTE_KEYS or not isinstance(body.get("accion"), str):
            raise GatewayError(
                400,
                "argumentos_invalidos",
                "El cuerpo debe tener «accion» (texto) y opcionalmente «argumentos» (objeto).",
            )
    except (GatewayError, HTTPException) as exc:
        return exception_response(exc)
    return await _respond(body["accion"], body.get("argumentos", {}), user)


@router.post(
    "/api/ia/v1/actions/{action_name}",
    dependencies=[Depends(require_gateway_token), Depends(require_csrf)],
)
async def ia_gateway_action(
    action_name: str, request: Request, user: dict = Depends(require_gateway_token)
):
    try:
        body = await _read_arguments(request)
    except HTTPException as exc:
        return exception_response(exc, accion=action_name if catalog.get_action(action_name) else None)
    return await _respond(action_name, body, user)


@router.post(
    "/api/ia/v1/mcp",
    dependencies=[Depends(require_gateway_token), Depends(require_csrf)],
)
async def ia_gateway_mcp(request: Request, user: dict = Depends(require_gateway_token)):
    try:
        return await jsonrpc.handle(request, adapters.require_gateway_user(user))
    except Exception as exc:
        return exception_response(exc)


def _method_not_allowed() -> JSONResponse:
    return error_response(GatewayError(405, "metodo_no_permitido", headers={"Allow": "POST"}))


@router.get("/api/ia/v1/mcp", dependencies=[Depends(require_gateway_token)])
async def ia_gateway_mcp_stream(user: dict = Depends(require_gateway_token)):
    return _method_not_allowed()


@router.delete(
    "/api/ia/v1/mcp",
    dependencies=[Depends(require_gateway_token), Depends(require_csrf)],
)
async def ia_gateway_mcp_session(user: dict = Depends(require_gateway_token)):
    return _method_not_allowed()


__all__ = ("router",)

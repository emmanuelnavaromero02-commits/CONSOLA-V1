from __future__ import annotations

import os
from typing import Any

from fastapi import Request

from app.services.mcp_gateway import adapters, catalog
from app.services.mcp_gateway.errors import GatewayError
from app.version import app_version


GATEWAY_PREFIX = "/api/ia/v1"
OPENAPI_VERSION = "3.1.0"
SECURITY_SCHEME = "tokenPersonal"
ERROR_STATUSES = ("400", "401", "403", "404", "409", "413", "429", "500", "502", "503", "504")
PRODUCTION_ENVS = frozenset({"production", "prod", "staging"})
MISSING_PUBLIC_URL = "La pasarela no tiene configurada su dirección pública."

SUCCESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "const": True},
        "accion": {"type": "string"},
        "espacio_de_trabajo": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "nombre": {"type": "string"}},
        },
        "resumen": {"type": "string"},
        "datos": {"type": "object"},
        "truncado": {"type": "boolean"},
        "solicitud_id": {"type": ["string", "null"]},
        "generado_en": {"type": "string"},
    },
    "required": ["ok", "accion", "resumen", "datos", "truncado"],
}
ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean", "const": False},
        "accion": {"type": ["string", "null"]},
        "error": {
            "type": "object",
            "properties": {
                "codigo": {"type": "string"},
                "mensaje": {"type": "string"},
                "reintentable": {"type": "boolean"},
            },
            "required": ["codigo", "mensaje", "reintentable"],
        },
        "datos": {"type": "object"},
        "solicitud_id": {"type": ["string", "null"]},
    },
    "required": ["ok", "error"],
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in PRODUCTION_ENVS


def server_url(request: Request) -> str:
    base = adapters.public_base_url()
    if base:
        return base.rstrip("/") + GATEWAY_PREFIX
    if _is_production():
        raise GatewayError(503, "servicio_no_disponible", MISSING_PUBLIC_URL)
    return str(request.base_url).rstrip("/") + GATEWAY_PREFIX


def _operation(action: catalog.GatewayAction) -> dict[str, Any]:
    schema = action.schema_copy()
    responses: dict[str, Any] = {
        "200": {
            "description": "Resultado de la acción.",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RespuestaExitosa"}}},
        }
    }
    for status in ERROR_STATUSES:
        responses[status] = {
            "description": "Error con mensaje en español.",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RespuestaError"}}},
        }
    return {
        "operationId": action.name,
        "summary": action.title,
        "description": action.description,
        "x-openai-isConsequential": action.consequential,
        "requestBody": {
            "required": bool(schema.get("required")),
            "content": {"application/json": {"schema": schema}},
        },
        "responses": responses,
    }


def build_document(*, url: str, read_only: bool = False) -> dict[str, Any]:
    actions = [
        action
        for action in catalog.ACTIONS
        if not read_only or action.scope == catalog.SCOPE_READ
    ]
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Pasarela de IA de ΩMEGA",
            "version": app_version(),
            "description": (
                "Consultas agregadas y acciones supervisadas sobre el espacio de trabajo ligado "
                "al token personal. Todas las respuestas están en español."
            ),
        },
        "servers": [{"url": url}],
        "security": [{SECURITY_SCHEME: []}],
        "paths": {f"/actions/{action.name}": {"post": _operation(action)} for action in actions},
        "components": {
            "securitySchemes": {
                SECURITY_SCHEME: {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "omega_pat",
                    "description": "Token personal creado en Mi acceso.",
                }
            },
            "schemas": {"RespuestaExitosa": SUCCESS_SCHEMA, "RespuestaError": ERROR_SCHEMA},
        },
    }


__all__ = ("GATEWAY_PREFIX", "build_document", "server_url")

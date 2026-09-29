from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.middleware.request_id import request_id_var


MESSAGES: dict[str, str] = {
    "argumentos_invalidos": "Los argumentos no son válidos.",
    "solicitud_invalida": "La solicitud no es válida.",
    "token_invalido": "El token no es válido.",
    "alcance_insuficiente": (
        "Este token es de solo lectura; crea uno con alcance de lectura y acciones "
        "para ejecutar esta acción."
    ),
    "permiso_insuficiente": "Tu usuario no tiene permiso para esta acción en este espacio de trabajo.",
    "fuente_no_habilitada": "Esa fuente de datos no está habilitada en este espacio de trabajo.",
    "csrf_invalido": "La verificación CSRF de la solicitud falló.",
    "no_encontrado": "No se encontró el recurso solicitado.",
    "metodo_no_permitido": "Este método no está permitido en esta ruta.",
    "conflicto": "La operación entra en conflicto con otra en curso.",
    "nombre_en_uso": "Ya existe una aplicación con ese nombre en este espacio de trabajo.",
    "solicitud_demasiado_grande": "La solicitud excede el tamaño permitido.",
    "limite_de_uso": "Se alcanzó el límite de uso; intenta de nuevo en unos minutos.",
    "operacion_en_curso": (
        "Ya hay una operación en curso que impide iniciar esta; espera a que termine."
    ),
    "servicio_no_disponible": (
        "El servicio no está disponible en este momento; intenta de nuevo en unos minutos."
    ),
    "tiempo_agotado": (
        "La operación tardó demasiado; la operación podría seguir en curso. "
        "Consulta su estado antes de reintentar."
    ),
    "error_interno": (
        "Ocurrió un error interno. Comparte el identificador de solicitud con soporte."
    ),
}
RETRYABLE_CODES = frozenset(
    {"limite_de_uso", "operacion_en_curso", "servicio_no_disponible", "tiempo_agotado"}
)
STATUS_CODES: dict[int, str] = {
    400: "argumentos_invalidos",
    401: "token_invalido",
    403: "permiso_insuficiente",
    404: "no_encontrado",
    405: "metodo_no_permitido",
    409: "conflicto",
    411: "argumentos_invalidos",
    413: "solicitud_demasiado_grande",
    422: "argumentos_invalidos",
    429: "limite_de_uso",
    502: "servicio_no_disponible",
    503: "servicio_no_disponible",
    504: "tiempo_agotado",
}
REASON_COPY: dict[str, str] = {
    "extract_all_already_running": (
        "Ya hay una extracción completa en curso para esta fuente; consulta su avance "
        "antes de iniciar otra."
    ),
    "too_many_active_entity_extracts": (
        "Hay demasiadas extracciones individuales en curso; espera a que terminen."
    ),
    "backpressure_unavailable": (
        "No se pudo reservar un turno de extracción en este momento; intenta de nuevo "
        "en unos minutos."
    ),
    "dag_unavailable": (
        "El proceso de extracción no está disponible en este momento; intenta de nuevo "
        "en unos minutos o avisa a un administrador."
    ),
    "dag_paused_by_operator": (
        "Un operador de la plataforma pausó este proceso; pide a un administrador que lo reanude."
    ),
    "stale_runs_require_recovery": (
        "Hay corridas anteriores atascadas en cola; deben cerrarse antes de lanzar una nueva extracción."
    ),
    "foreign_backlog_requires_platform_recovery": (
        "El proceso tiene corridas atascadas de otro espacio de trabajo; un administrador "
        "de la plataforma debe liberarlas."
    ),
    "auto_unpause_disabled": (
        "El proceso está en pausa y la reactivación automática está desactivada; pide a un "
        "administrador que lo reanude."
    ),
    "plan_changed": "Las corridas cambiaron desde la revisión; revisa el plan de nuevo.",
    "connection_check_failed": "Credenciales no válidas o incompletas en la Bóveda de Accesos.",
    "connection_probe_failed": "No se pudo validar la conexión con el origen.",
    "airflow_trigger_failed": "No se pudo iniciar la extracción en el orquestador.",
    "sync_stale_timeout": (
        "La sincronización agotó el tiempo de espera en el orquestador; inicia una nueva."
    ),
}
DETAIL_CODES: dict[str, str] = {
    "cartridge not allowed for active workspace": "fuente_no_habilitada",
    "cartridge_not_allowed": "fuente_no_habilitada",
    "Invalid JSON body": "solicitud_invalida",
    "Invalid Content-Length": "solicitud_invalida",
    "csrf token invalid or missing": "csrf_invalido",
}
DETAIL_MESSAGES: dict[str, str] = {
    "sync run not found": "No se encontró esa ejecución de extracción.",
    "view not found": "No se encontró esa vista de indicadores.",
    "Invalid JSON body": (
        "El cuerpo de la solicitud no es un objeto JSON válido o excede la profundidad permitida."
    ),
    "csrf token invalid or missing": (
        "La verificación CSRF de la solicitud falló; con un token personal no envíes "
        "cookies de sesión del navegador."
    ),
}
_MAX_PUBLIC_MESSAGE_CHARS = 300
_FIELD_MESSAGES: dict[str, str] = {
    "missing": "es obligatorio",
    "extra_forbidden": "no está permitido",
    "string_too_short": "es demasiado corto",
    "string_too_long": "es demasiado largo",
    "string_pattern_mismatch": "tiene un formato inválido",
    "string_type": "debe ser texto",
    "int_type": "debe ser un número entero",
    "int_parsing": "debe ser un número entero",
    "int_from_float": "debe ser un número entero",
    "bool_type": "debe ser verdadero o falso",
    "bool_parsing": "debe ser verdadero o falso",
    "list_type": "debe ser una lista",
    "too_short": "tiene muy pocos elementos",
    "too_long": "tiene demasiados elementos",
    "literal_error": "no es un valor permitido",
    "enum": "no es un valor permitido",
    "greater_than_equal": "está por debajo del mínimo permitido",
    "less_than_equal": "excede el máximo permitido",
    "dict_type": "debe ser un objeto",
    "model_type": "debe ser un objeto",
}


class GatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        codigo: str,
        mensaje: str | None = None,
        *,
        reintentable: bool | None = None,
        headers: Mapping[str, str] | None = None,
        datos: Mapping[str, Any] | None = None,
    ) -> None:
        text = mensaje or MESSAGES.get(codigo) or MESSAGES["error_interno"]
        super().__init__(text)
        self.status_code = int(status_code)
        self.codigo = codigo
        self.mensaje = text
        self.reintentable = (
            codigo in RETRYABLE_CODES if reintentable is None else bool(reintentable)
        )
        self.headers = dict(headers or {})
        self.datos = dict(datos) if datos else None


class GatewayHTTPException(HTTPException):
    def __init__(
        self,
        status_code: int,
        codigo: str,
        mensaje: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, detail=mensaje or MESSAGES.get(codigo), headers=dict(headers or {}))
        self.gateway_error = GatewayError(status_code, codigo, mensaje, headers=headers)


def current_request_id() -> str | None:
    return request_id_var.get()


def _safe_public_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > _MAX_PUBLIC_MESSAGE_CHARS:
        return None
    return text


def _from_detail(status_code: int, detail: object, headers: Mapping[str, str] | None) -> GatewayError:
    codigo = STATUS_CODES.get(status_code, "error_interno" if status_code >= 500 else "argumentos_invalidos")
    mensaje: str | None = None
    if isinstance(detail, Mapping):
        reason = str(detail.get("reason") or detail.get("error") or "")
        if reason in REASON_COPY:
            mensaje = REASON_COPY[reason]
        elif reason in DETAIL_CODES:
            codigo = DETAIL_CODES[reason]
        else:
            mensaje = _safe_public_message(detail.get("public_message"))
    elif isinstance(detail, str):
        if detail in DETAIL_CODES:
            codigo = DETAIL_CODES[detail]
        mensaje = DETAIL_MESSAGES.get(detail)
    extra_headers = {}
    if headers and status_code == 405 and headers.get("Allow"):
        extra_headers["Allow"] = headers["Allow"]
    if status_code == 401:
        extra_headers["WWW-Authenticate"] = "Bearer"
    return GatewayError(status_code if status_code >= 400 else 500, codigo, mensaje, headers=extra_headers)


def validation_message(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = [str(item) for item in error.get("loc") or () if not isinstance(item, int)]
        field = ".".join(loc) or "argumentos"
        reason = _FIELD_MESSAGES.get(str(error.get("type") or ""), "no es válido")
        parts.append(f"«{field}» {reason}")
        if len(parts) >= 5:
            break
    if not parts:
        return MESSAGES["argumentos_invalidos"]
    return "Argumentos inválidos: " + "; ".join(parts) + "."


def gateway_error_from_exception(exc: BaseException) -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc
    if isinstance(exc, GatewayHTTPException):
        return exc.gateway_error
    if isinstance(exc, ValidationError):
        return GatewayError(400, "argumentos_invalidos", validation_message(exc))
    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        return _from_detail(exc.status_code, exc.detail, getattr(exc, "headers", None))
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return GatewayError(504, "tiempo_agotado")
    tool_policy_error = _tool_policy_error_type()
    if tool_policy_error is not None and isinstance(exc, tool_policy_error):
        return GatewayError(
            400,
            "argumentos_invalidos",
            "Los argumentos contienen contenido o estructura no permitidos.",
        )
    app_forge_error = _app_forge_error_type()
    if app_forge_error is not None and isinstance(exc, app_forge_error):
        return _from_app_forge(str(exc))
    return GatewayError(500, "error_interno")


_APP_FORGE_SAFE_PREFIXES = (
    "Se requiere al menos un dataset autorizado.",
    "Máximo ",
    "Nombre de dataset inválido",
    "Se requiere el objetivo",
    "El nombre de la app es inválido",
    "Se requiere un workspace activo",
    "No se pudo leer el esquema del dataset",
    "El dataset '",
    "La generación devolvió un HTML demasiado corto",
    "La app generada referencia",
    "La app generada usa import()",
)


def _from_app_forge(message: str) -> GatewayError:
    if message.startswith("Tu usuario no tiene el permiso"):
        return GatewayError(403, "permiso_insuficiente")
    if message.startswith(_APP_FORGE_SAFE_PREFIXES) and len(message) <= _MAX_PUBLIC_MESSAGE_CHARS:
        return GatewayError(400, "argumentos_invalidos", message)
    return GatewayError(
        503,
        "servicio_no_disponible",
        "No se pudo generar o publicar la aplicación en este momento; no se publicó nada.",
    )


def _tool_policy_error_type() -> type[BaseException] | None:
    try:
        from app.services.tool_policy import ToolPolicyError
    except Exception:  # pragma: no cover
        return None
    return ToolPolicyError


def _app_forge_error_type() -> type[BaseException] | None:
    try:
        from app.services.app_forge import AppForgeError
    except Exception:  # pragma: no cover
        return None
    return AppForgeError


def error_body(error: GatewayError, *, accion: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "accion": accion,
        "error": {
            "codigo": error.codigo,
            "mensaje": error.mensaje,
            "reintentable": error.reintentable,
        },
        "solicitud_id": current_request_id(),
    }
    if error.datos:
        body["datos"] = error.datos
    return body


def error_response(
    error: GatewayError, *, accion: str | None = None, status_code: int | None = None
) -> JSONResponse:
    headers = {"Cache-Control": "no-store", **error.headers}
    return JSONResponse(
        error_body(error, accion=accion),
        status_code=status_code or error.status_code,
        headers=headers,
    )


def exception_response(exc: BaseException, *, accion: str | None = None) -> JSONResponse:
    return error_response(gateway_error_from_exception(exc), accion=accion)


__all__ = (
    "GatewayError",
    "GatewayHTTPException",
    "MESSAGES",
    "REASON_COPY",
    "current_request_id",
    "error_body",
    "error_response",
    "exception_response",
    "gateway_error_from_exception",
    "validation_message",
)

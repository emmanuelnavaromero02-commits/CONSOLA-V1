from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.services import access_tokens
from app.services.mcp_gateway.errors import (
    GatewayError,
    GatewayHTTPException,
    error_response,
    exception_response,
)


logger = logging.getLogger(__name__)

GATEWAY_PREFIX = "/api/ia/v1"
PUBLIC_GATEWAY_PATHS = frozenset({"/api/ia/v1/openapi.json"})
AUTH_IP_RATE_LIMIT = "ia_gateway:auth_ip"
AUTH_FAILURE_RATE_LIMIT = "ia_gateway:auth_failures"
PERSONAL_TOKEN_REQUIRED = "Esta ruta requiere un token personal"
PERSONAL_TOKEN_OUTSIDE_GATEWAY = "Este token solo es válido en la pasarela de IA"
TOKEN_INVALID = "El token no es válido."
LOST_WORKSPACE_ACCESS = "El token ya no tiene acceso a su espacio de trabajo"
GATEWAY_DISABLED = "La pasarela de IA está desactivada temporalmente."
TOO_MANY_FAILURES = (
    "Demasiados intentos con tokens no válidos desde esta dirección; "
    "intenta de nuevo en unos minutos."
)
STATUS_MESSAGES: dict[str, str] = {
    "vencido": "El token venció; genera uno nuevo en Mi acceso.",
    "revocado": "El token fue revocado; genera uno nuevo en Mi acceso.",
    "usuario_inactivo": "La cuenta asociada a este token está desactivada.",
    "cambio_de_contrasena": (
        "La cuenta asociada a este token debe cambiar su contraseña antes de usarlo."
    ),
    "espacio_inexistente": "El espacio de trabajo de este token ya no existe.",
}
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})

ApplyHeaders = Callable[[Response, str], Response]
RateLimitIp = Callable[[Request, str], Awaitable[None]]
RateLimitSurface = Callable[[Request, str, Any], Awaitable[None]]
RateLimitGateway = Callable[[Mapping[str, Any]], Awaitable[None]]


def is_gateway_path(path: str) -> bool:
    return path == GATEWAY_PREFIX or path.startswith(GATEWAY_PREFIX + "/")


def is_public_gateway_path(path: str) -> bool:
    return path in PUBLIC_GATEWAY_PATHS


def gateway_enabled(environ: Mapping[str, str] | None = None) -> bool:
    raw = (environ if environ is not None else os.environ).get("IA_GATEWAY_ENABLED")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def _bearer_credential(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, credential = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credential.strip() or None


def carries_personal_token(request: Request) -> bool:
    return access_tokens.has_token_prefix(_bearer_credential(request))


def _unauthorized(message: str) -> GatewayError:
    return GatewayError(401, "token_invalido", message, headers=_BEARER_CHALLENGE)


async def gateway_user(resolved: Mapping[str, Any]) -> dict[str, Any]:
    from app import dependencies as deps

    base: dict[str, Any] = {
        "id": resolved["user_id"],
        "email": resolved.get("email"),
        "name": resolved.get("name"),
        "role": resolved.get("role"),
        "is_active": True,
        "must_change_password": False,
        "tenant_id": resolved.get("user_tenant_id"),
    }
    workspace_id = str(resolved.get("workspace_id") or "")
    tenant_id = str(resolved.get("tenant_id") or "")
    options = await deps._workspace_access_options(base)
    bound = next(
        (
            dict(option)
            for option in options or []
            if str(option.get("workspace_id") or "") == workspace_id
        ),
        None,
    )
    if bound is None or str(bound.get("tenant_id") or "") != tenant_id:
        raise _unauthorized(LOST_WORKSPACE_ACCESS)
    cartridges = await deps._workspace_cartridges(workspace_id, user_id=base["id"])
    return {
        **base,
        "auth_method": "pat",
        "access_token_id": str(resolved.get("token_id") or ""),
        "access_token_prefix": str(resolved.get("token_prefix") or ""),
        "access_token_name": resolved.get("token_name"),
        "access_token_scopes": [str(item) for item in resolved.get("scopes") or []],
        "access_token_expires_at": resolved.get("expires_at"),
        "workspace_role": bound.get("workspace_role"),
        "active_workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_name": bound.get("workspace_name") or resolved.get("workspace_name"),
        "workspaces": [bound],
        "allowed_cartridges": [str(item) for item in cartridges or []],
    }


async def _failed_resolution(
    request: Request, rate_limit_ip: RateLimitIp, message: str
) -> GatewayError:
    try:
        await rate_limit_ip(request, AUTH_FAILURE_RATE_LIMIT)
    except HTTPException:
        return GatewayError(429, "limite_de_uso", TOO_MANY_FAILURES)
    return _unauthorized(message)


async def middleware_response(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    path: str,
    is_public_path: Callable[[str], bool],
    apply_security_headers: ApplyHeaders,
    client_ip: Callable[[Request], str],
    rate_limit_ip: RateLimitIp,
    rate_limit_surface: RateLimitSurface,
    rate_limit_gateway: RateLimitGateway,
) -> Response | None:
    if not is_gateway_path(path):
        if carries_personal_token(request) and not is_public_path(path):
            return apply_security_headers(
                JSONResponse(
                    {"detail": PERSONAL_TOKEN_OUTSIDE_GATEWAY},
                    status_code=401,
                    headers=_BEARER_CHALLENGE,
                ),
                path,
            )
        return None
    try:
        response = await _gateway_response(
            request,
            call_next,
            path=path,
            client_ip=client_ip,
            rate_limit_ip=rate_limit_ip,
            rate_limit_surface=rate_limit_surface,
            rate_limit_gateway=rate_limit_gateway,
        )
    except GatewayError as exc:
        response = error_response(exc)
    except Exception:
        logger.exception("ia gateway request failed")
        response = error_response(GatewayError(500, "error_interno"))
    return apply_security_headers(response, path)


async def _gateway_response(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    path: str,
    client_ip: Callable[[Request], str],
    rate_limit_ip: RateLimitIp,
    rate_limit_surface: RateLimitSurface,
    rate_limit_gateway: RateLimitGateway,
) -> Response:
    if not gateway_enabled():
        raise GatewayError(503, "servicio_no_disponible", GATEWAY_DISABLED)
    if is_public_gateway_path(path):
        request.state.user = None
        try:
            await rate_limit_surface(request, path, None)
        except HTTPException as exc:
            return exception_response(exc)
        return await call_next(request)
    credential = _bearer_credential(request)
    if not access_tokens.has_token_prefix(credential):
        raise _unauthorized(PERSONAL_TOKEN_REQUIRED)
    if not access_tokens.looks_like_token(credential):
        raise await _failed_resolution(request, rate_limit_ip, TOKEN_INVALID)
    try:
        await rate_limit_ip(request, AUTH_IP_RATE_LIMIT)
    except HTTPException as exc:
        return exception_response(exc)
    try:
        resolved = await access_tokens.resolve_token(str(credential), client_ip(request))
    except Exception:
        logger.warning("personal access token resolution unavailable")
        raise GatewayError(503, "servicio_no_disponible") from None
    if resolved is None:
        raise await _failed_resolution(request, rate_limit_ip, TOKEN_INVALID)
    status = str(resolved.get("status") or "")
    if status != "activo":
        raise await _failed_resolution(
            request, rate_limit_ip, STATUS_MESSAGES.get(status, TOKEN_INVALID)
        )
    try:
        user = await gateway_user(resolved)
    except GatewayError as exc:
        if exc.status_code == 401:
            raise await _failed_resolution(request, rate_limit_ip, exc.mensaje) from None
        raise
    except Exception:
        logger.warning("personal access token workspace enrichment unavailable")
        raise GatewayError(503, "servicio_no_disponible") from None
    request.state.user = user
    try:
        await rate_limit_gateway(user)
    except HTTPException as exc:
        return exception_response(exc)
    return await call_next(request)


async def require_gateway_token(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if (
        not is_gateway_path(request.url.path)
        or not isinstance(user, dict)
        or user.get("auth_method") != "pat"
        or not user.get("active_workspace_id")
        or not user.get("access_token_id")
    ):
        raise GatewayHTTPException(
            401, "token_invalido", PERSONAL_TOKEN_REQUIRED, headers=_BEARER_CHALLENGE
        )
    return user


__all__ = (
    "GATEWAY_PREFIX",
    "PUBLIC_GATEWAY_PATHS",
    "carries_personal_token",
    "gateway_enabled",
    "gateway_user",
    "is_gateway_path",
    "is_public_gateway_path",
    "middleware_response",
    "require_gateway_token",
)

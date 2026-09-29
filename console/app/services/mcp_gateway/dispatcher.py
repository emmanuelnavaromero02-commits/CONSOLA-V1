from __future__ import annotations

import asyncio
import copy
import functools
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from app.services import audit_service, tool_policy
from app.services.mcp_gateway import adapters, catalog
from app.services.mcp_gateway.actions import HANDLERS, ActionResult
from app.services.mcp_gateway.errors import (
    GatewayError,
    current_request_id,
    error_body,
    gateway_error_from_exception,
    validation_message,
)
from app.services.rate_limiter import get_rate_limiter
from app.services.request_rate_limits import rate_limit_gateway


logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 48 * 1024
MAX_LONG_RUNNING = 4
MAX_LONG_RUNNING_PER_WORKSPACE = 1
KEEPALIVE_SECONDS = 15.0
UNKNOWN_ACTION = "No existe esa acción en la pasarela de IA."
BUSY_MESSAGE = "Hay demasiadas operaciones largas en curso; intenta de nuevo en unos minutos."
WORKSPACE_BUSY_MESSAGE = (
    "Ya hay una operación larga en curso en este espacio de trabajo; espera a que termine "
    "antes de iniciar otra."
)
OVERSIZED_MESSAGE = "La respuesta excedía el tamaño permitido; acota la consulta."
_LONG_RUNNING_TASKS: set[asyncio.Task[Any]] = set()
_WORKSPACE_TASKS: dict[str, set[asyncio.Task[Any]]] = {}


@dataclass(frozen=True)
class PreparedAction:
    action: catalog.GatewayAction
    args: BaseModel
    raw_args: dict[str, Any]


def long_running_count(workspace_id: str | None = None) -> int:
    if workspace_id is None:
        return len(_LONG_RUNNING_TASKS)
    return len(_WORKSPACE_TASKS.get(str(workspace_id), ()))


def _workspace_key(user: dict[str, Any]) -> str:
    return str(user.get("active_workspace_id") or "")


def _ensure_long_running_slot(user: dict[str, Any]) -> None:
    if len(_WORKSPACE_TASKS.get(_workspace_key(user), ())) >= MAX_LONG_RUNNING_PER_WORKSPACE:
        raise GatewayError(429, "operacion_en_curso", WORKSPACE_BUSY_MESSAGE)
    if len(_LONG_RUNNING_TASKS) >= MAX_LONG_RUNNING:
        raise GatewayError(429, "limite_de_uso", BUSY_MESSAGE)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _largest_list(value: Any) -> list[Any] | None:
    best: list[Any] | None = None
    best_size = -1
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            if len(node) > 1:
                size = _encoded_size(node)
                if size > best_size:
                    best, best_size = node, size
            stack.extend(node)
    return best


def clip_datos(datos: dict[str, Any], limit: int = MAX_RESPONSE_BYTES) -> tuple[dict[str, Any], bool]:
    if _encoded_size(datos) <= limit:
        return datos, False
    data = copy.deepcopy(datos)
    for _ in range(128):
        target = _largest_list(data)
        if target is None:
            break
        del target[max(1, len(target) // 2):]
        if _encoded_size(data) <= limit:
            return data, True
    return {"mensaje": OVERSIZED_MESSAGE}, True


def success_envelope(action_name: str, user: dict[str, Any], result: ActionResult) -> dict[str, Any]:
    datos, clipped = clip_datos(result.datos if isinstance(result.datos, dict) else {})
    return {
        "ok": True,
        "accion": action_name,
        "espacio_de_trabajo": adapters.workspace_summary(user),
        "resumen": result.resumen,
        "datos": datos,
        "truncado": bool(result.truncado or clipped),
        "solicitud_id": current_request_id(),
        "generado_en": utc_now(),
    }


async def _audit(
    user: dict[str, Any],
    action_name: str,
    raw_args: Any,
    *,
    status: str,
    risk_level: str,
    codigo: str | None = None,
    duration_ms: int | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "accion": action_name,
        "token_id": user.get("access_token_id"),
        "token_prefix": user.get("access_token_prefix"),
        "workspace_id": user.get("active_workspace_id"),
    }
    if codigo:
        metadata["codigo"] = codigo
    if duration_ms is not None:
        metadata["duracion_ms"] = duration_ms
    try:
        tool_args = tool_policy.clip_args(
            tool_policy.scrub_args(raw_args if isinstance(raw_args, dict) else {})
        )
    except RecursionError:
        tool_args = {"_truncated": True, "reason": "tool args too deeply nested"}
    try:
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="ia_gateway.action",
            resource_type="ia_gateway_action",
            resource_id=action_name,
            status=status,
            metadata=metadata,
            tool_name=f"ia_gateway__{action_name}",
            tool_args=tool_args,
            tool_result_status=status,
            risk_level=risk_level,
        )
    except Exception:
        logger.warning("ia gateway audit event could not be recorded")


def _risk(action: catalog.GatewayAction | None) -> str:
    return "read" if action is None or action.read_only else "write"


async def prepare(action_name: str, raw_args: Any, user: Any) -> PreparedAction:
    checked = adapters.require_gateway_user(user)
    action = catalog.get_action(action_name)
    if action is None:
        raise GatewayError(404, "no_encontrado", UNKNOWN_ACTION)
    denial: GatewayError | None = None
    if not catalog.scope_allows(action, catalog.token_scopes(checked)):
        denial = GatewayError(403, "alcance_insuficiente")
    elif not catalog.permission_allows(action, checked):
        denial = GatewayError(403, "permiso_insuficiente")
    elif not catalog.source_allows(action, checked):
        denial = GatewayError(403, "fuente_no_habilitada")
    if denial is not None:
        await _audit(checked, action.name, raw_args, status="denied", risk_level=_risk(action), codigo=denial.codigo)
        raise denial
    args = {} if raw_args is None else raw_args
    if not isinstance(args, dict):
        raise GatewayError(400, "argumentos_invalidos", "Los argumentos deben ser un objeto JSON.")
    try:
        tool_policy.validate_tool_args(action.name, args, action.schema_copy(), risk_level=_risk(action))
    except tool_policy.ToolPolicyError:
        try:
            action.args_model.model_validate(args)
        except ValidationError as exc:
            raise GatewayError(400, "argumentos_invalidos", validation_message(exc)) from None
        raise
    parsed = action.args_model.model_validate(args)
    if action.long_running:
        _ensure_long_running_slot(checked)
    await rate_limit_gateway(checked, action.name, limiter_factory=get_rate_limiter)
    return PreparedAction(action=action, args=parsed, raw_args=dict(args))


async def _perform(prepared: PreparedAction, user: dict[str, Any]) -> ActionResult:
    started = time.monotonic()
    action = prepared.action
    try:
        result = await HANDLERS[action.name](user, prepared.args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = gateway_error_from_exception(exc)
        if error.codigo == "error_interno":
            logger.exception("ia gateway action failed accion=%s", action.name)
        await _audit(
            user,
            action.name,
            prepared.raw_args,
            status="error",
            risk_level=_risk(action),
            codigo=error.codigo,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise error from None
    await _audit(
        user,
        action.name,
        prepared.raw_args,
        status="success",
        risk_level=_risk(action),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def _forget(workspace_id: str, task: asyncio.Task[Any]) -> None:
    _LONG_RUNNING_TASKS.discard(task)
    tasks = _WORKSPACE_TASKS.get(workspace_id)
    if tasks is not None:
        tasks.discard(task)
        if not tasks:
            _WORKSPACE_TASKS.pop(workspace_id, None)
    if not task.cancelled():
        task.exception()


async def _run_detached(prepared: PreparedAction, user: dict[str, Any]) -> ActionResult:
    _ensure_long_running_slot(user)
    workspace_id = _workspace_key(user)
    task = asyncio.create_task(_perform(prepared, user))
    _LONG_RUNNING_TASKS.add(task)
    _WORKSPACE_TASKS.setdefault(workspace_id, set()).add(task)
    task.add_done_callback(functools.partial(_forget, workspace_id))
    try:
        return await asyncio.wait_for(asyncio.shield(task), prepared.action.timeout_seconds)
    except (asyncio.TimeoutError, TimeoutError):
        raise GatewayError(504, "tiempo_agotado") from None


async def run(prepared: PreparedAction, user: dict[str, Any]) -> dict[str, Any]:
    if prepared.action.long_running:
        result = await _run_detached(prepared, user)
    else:
        try:
            result = await asyncio.wait_for(
                _perform(prepared, user), prepared.action.timeout_seconds
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise GatewayError(504, "tiempo_agotado") from None
    return success_envelope(prepared.action.name, user, result)


async def run_envelope(prepared: PreparedAction, user: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        return 200, await run(prepared, user)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = gateway_error_from_exception(exc)
        return error.status_code, error_body(error, accion=prepared.action.name)


async def execute(action_name: str, raw_args: Any, user: Any) -> tuple[int, dict[str, Any]]:
    try:
        prepared = await prepare(action_name, raw_args, user)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = gateway_error_from_exception(exc)
        return error.status_code, error_body(error, accion=action_name if catalog.get_action(action_name) else None)
    return await run_envelope(prepared, user)


async def keepalive_stream(
    work: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    *,
    heartbeat: bytes,
    render: Callable[[dict[str, Any]], bytes],
) -> AsyncIterator[bytes]:
    waiter = asyncio.ensure_future(work())
    try:
        while True:
            done, _pending = await asyncio.wait({waiter}, timeout=KEEPALIVE_SECONDS)
            if done:
                break
            yield heartbeat
        _status, envelope = waiter.result()
        yield render(envelope)
    finally:
        if not waiter.done():
            waiter.cancel()


__all__ = (
    "KEEPALIVE_SECONDS",
    "MAX_LONG_RUNNING",
    "MAX_LONG_RUNNING_PER_WORKSPACE",
    "MAX_RESPONSE_BYTES",
    "PreparedAction",
    "clip_datos",
    "execute",
    "keepalive_stream",
    "long_running_count",
    "prepare",
    "run",
    "run_envelope",
    "success_envelope",
)

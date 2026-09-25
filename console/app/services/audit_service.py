import json
import logging
import sys
from typing import Any

from app.middleware.request_id import request_id_var
from app.services import auth

logger = logging.getLogger(__name__)


def _audit_auth_module():
    module = sys.modules.get(__name__)
    if module is not None and hasattr(module, "auth"):
        return module.auth
    return auth


def _audit_user_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


async def _audit_events_has_request_id(pool) -> bool:
    return bool(
        await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'audit_events'
                   AND column_name = 'request_id'
            )
            """
        )
    )


async def record_event(
    user_id: int | None = None,
    email: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result_status: str | None = None,
    risk_level: str | None = None,
    conversation_id: str | None = None,
    critical: bool = False,
    connection: Any | None = None,
) -> None:
    try:
        db = connection if connection is not None else await _audit_auth_module().pool()
        exists = await db.fetchval("SELECT to_regclass('public.audit_events')")
        if not exists:
            if critical:
                raise RuntimeError(
                    "critical audit table public.audit_events is missing"
                )
            return
        meta_json = json.dumps(metadata, default=str) if metadata is not None else None
        tool_args_json = (
            json.dumps(tool_args, default=str) if tool_args is not None else None
        )
        event_user_id = _audit_user_id(user_id)
        event_request_id = request_id or request_id_var.get()
        has_request_id = await _audit_events_has_request_id(db)

        if has_request_id:
            await db.execute(
                """
                INSERT INTO audit_events
                (user_id, email, action, resource_type, resource_id,
                 ip, user_agent, status, request_id, metadata,
                 tool_name, tool_args, tool_result_status, risk_level, conversation_id)
                VALUES ($1, $2, $3, $4, $5,
                        $6, $7, $8, $9, $10::jsonb,
                        $11, $12::jsonb, $13, $14, $15)
                ON CONFLICT (user_id, action, resource_id, created_at)
                  DO NOTHING
                """,
                event_user_id,
                email,
                action,
                resource_type,
                resource_id,
                ip,
                user_agent,
                status,
                event_request_id,
                meta_json,
                tool_name,
                tool_args_json,
                tool_result_status,
                risk_level,
                conversation_id,
            )
        else:
            await db.execute(
                """
                INSERT INTO audit_events
                (user_id, email, action, resource_type, resource_id,
                 ip, user_agent, status, metadata,
                 tool_name, tool_args, tool_result_status, risk_level, conversation_id)
                VALUES ($1, $2, $3, $4, $5,
                        $6, $7, $8, $9::jsonb,
                        $10, $11::jsonb, $12, $13, $14)
                ON CONFLICT (user_id, action, resource_id, created_at)
                  DO NOTHING
                """,
                event_user_id,
                email,
                action,
                resource_type,
                resource_id,
                ip,
                user_agent,
                status,
                meta_json,
                tool_name,
                tool_args_json,
                tool_result_status,
                risk_level,
                conversation_id,
            )
    except Exception as e:
        logger.error(f"Failed to record audit event: {e}", exc_info=True)
        if critical:
            raise

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
    # Sprint v1.41.0 (copilot scaffolding): when the future copilot invokes
    # a tool on behalf of the user, these columns describe which tool was
    # called, with what args, and what risk class. NULL for non-copilot
    # events (login, user CRUD, vault reveal, etc.). The matching columns
    # land in audit_events via infra/init/39_audit_tool_columns.sql.
    #
    # SECURITY: tool_args is persisted indefinitely as JSONB and indexed.
    # Callers MUST strip secrets (passwords, vault tokens, API keys, OAuth
    # bearer values, anything from /api/vault/secrets/*) before passing the
    # dict in. Replace sensitive values with "***" or drop the key.
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result_status: str | None = None,
    risk_level: str | None = None,
    conversation_id: str | None = None,
    critical: bool = False,
) -> None:
    """Durably record an audit event before returning.

    If the database operation fails, it logs the error without raising an
    exception for normal UI events. Critical callers (tool calls, approvals,
    destructive operations) must pass ``critical=True`` so the action fails
    closed when the audit trail cannot be written.

    ``tool_args`` must contain only non-sensitive parameters: scrub secrets,
    vault values, and credentials before invoking this function.
    """
    try:
        pool = await _audit_auth_module().pool()
        exists = await pool.fetchval("SELECT to_regclass('public.audit_events')")
        if not exists:
            if critical:
                raise RuntimeError("critical audit table public.audit_events is missing")
            return
        meta_json = json.dumps(metadata) if metadata is not None else None
        tool_args_json = json.dumps(tool_args) if tool_args is not None else None
        event_user_id = _audit_user_id(user_id)
        event_request_id = request_id or request_id_var.get()
        has_request_id = await _audit_events_has_request_id(pool)

        if has_request_id:
            await pool.execute(
                # v1.43.2 Claude B5: ON CONFLICT DO NOTHING swallows
                # exact-duplicate inserts (same user/action/resource at
                # the same created_at timestamp) so a retry of the same
                # admin action no longer inflates the forensic trail.
                # The constraint audit_events_dedup_uniq is added by
                # migration 44.
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
            await pool.execute(
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

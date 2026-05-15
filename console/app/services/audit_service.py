import asyncio
import json
import logging
from typing import Any

from app.services import auth

logger = logging.getLogger(__name__)

async def record_event(
    user_id: int | None = None,
    email: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    # Sprint v1.41.0 (copilot scaffolding): when the future copilot invokes
    # a tool on behalf of the user, these columns describe which tool was
    # called, with what args, and what risk class. NULL for non-copilot
    # events (login, user CRUD, vault reveal, etc.). The matching columns
    # land in audit_events via infra/init/39_audit_tool_columns.sql.
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    tool_result_status: str | None = None,
    risk_level: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """
    Asynchronously records an audit event without blocking the current request.
    If the database operation fails, it logs the error without raising an exception.
    """
    async def _insert_event() -> None:
        try:
            pool = await auth.pool()
            exists = await pool.fetchval("SELECT to_regclass('public.audit_events')")
            if not exists:
                return
            meta_json = json.dumps(metadata) if metadata is not None else None
            tool_args_json = json.dumps(tool_args) if tool_args is not None else None

            await pool.execute(
                """
                INSERT INTO audit_events
                (user_id, email, action, resource_type, resource_id,
                 ip, user_agent, status, metadata,
                 tool_name, tool_args, tool_result_status, risk_level, conversation_id)
                VALUES ($1, $2, $3, $4, $5,
                        $6, $7, $8, $9::jsonb,
                        $10, $11::jsonb, $12, $13, $14)
                """,
                user_id,
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

    # Schedule the database insert in the background
    asyncio.create_task(_insert_event())

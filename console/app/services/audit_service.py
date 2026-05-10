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
    metadata: dict[str, Any] | None = None
) -> None:
    """
    Asynchronously records an audit event without blocking the current request.
    If the database operation fails, it logs the error without raising an exception.
    """
    async def _insert_event() -> None:
        try:
            pool = await auth.pool()
            meta_json = json.dumps(metadata) if metadata is not None else None

            await pool.execute(
                """
                INSERT INTO audit_events
                (user_id, email, action, resource_type, resource_id, ip, user_agent, status, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                user_id,
                email,
                action,
                resource_type,
                resource_id,
                ip,
                user_agent,
                status,
                meta_json
            )
        except Exception as e:
            logger.error(f"Failed to record audit event: {e}", exc_info=True)

    # Schedule the database insert in the background
    asyncio.create_task(_insert_event())

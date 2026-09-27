from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.schemas.control_room_live import ControlRoomRefreshResponse
from app.services import audit_service, auth, control_room_service
from app.services.control_room.business_action_authority_policy import (
    authority_scope,
    require_write,
)
from app.services.db_scope import run_with_db_scope


async def refresh_control_room_state(
    user: Mapping[str, Any],
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ControlRoomRefreshResponse:
    """Explicit, audited persistence of the live observations the user can see."""
    require_write(user)
    _tenant_id, workspace_id = authority_scope(user)
    audited = False

    async def _audit(conn: Any, persisted: int, lapsed: int) -> None:
        nonlocal audited
        await audit_service.record_event(
            connection=conn,
            user_id=user.get("id"),
            email=user.get("email"),
            action="control_room.state.refresh",
            resource_type="control_room_workspace",
            resource_id=workspace_id,
            ip=ip,
            user_agent=user_agent,
            status="success",
            metadata={"persisted_rows": persisted, "lapsed_exceptions": lapsed},
            critical=True,
        )
        audited = True

    await control_room_service.refresh_dashboard_state(dict(user), on_persisted=_audit)
    if not audited:

        async def _audit_empty(
            conn: Any, _tenant_id: str | None, _workspace_id: str
        ) -> None:
            await _audit(conn, 0, 0)

        await run_with_db_scope(await auth.pool(), dict(user), _audit_empty)
    return ControlRoomRefreshResponse(refreshed_at=datetime.now(UTC))


__all__ = ("refresh_control_room_state",)

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.db_scope import scoped_db_for_user


async def require_visible(
    pool: Any,
    user: dict[str, Any],
    simulation_id: str,
) -> Any:
    """Confirm the simulation is readable after its write transaction commits."""
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
        row = await conn.fetchrow(
            """
            SELECT *
              FROM monte_carlo_simulations
             WHERE workspace_id = $1
               AND simulation_id = $2
            """,
            workspace_id,
            simulation_id,
        )
    if not row:
        raise HTTPException(503, "monte carlo simulation commit was not visible")
    return row

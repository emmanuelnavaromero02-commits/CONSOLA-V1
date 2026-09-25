from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import HTTPException


SET_SCOPE_SQL = (
    "SELECT set_config('app.tenant_id', $1, true), "
    "set_config('app.workspace_id', $2, true)"
)


def workspace_scope_from_user(user: dict | None) -> tuple[str | None, str]:

    data = user or {}
    tenant_id = data.get("active_tenant_id") or data.get("tenant_id")
    workspace_id = data.get("active_workspace_id") or data.get("workspace_id")
    workspace_text = str(workspace_id or "").strip()
    if not workspace_text:
        raise HTTPException(403, "active workspace is required")
    tenant_text = str(tenant_id).strip() if tenant_id else None
    return tenant_text or None, workspace_text


def _looks_like_asyncpg_pool(pool: Any) -> bool:
    return callable(getattr(type(pool), "acquire", None))


@asynccontextmanager
async def scoped_db(
    pool: Any, tenant_id: str | None, workspace_id: str
) -> AsyncIterator[Any]:

    workspace_text = str(workspace_id or "").strip()
    if not workspace_text:
        raise HTTPException(403, "active workspace is required")
    tenant_text = str(tenant_id or "").strip()

    if _looks_like_asyncpg_pool(pool):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_text, workspace_text)
                yield conn
        return

    execute = getattr(pool, "execute", None)
    if callable(execute):
        await execute(SET_SCOPE_SQL, tenant_text, workspace_text)
    yield pool


@asynccontextmanager
async def scoped_db_for_user(
    pool: Any, user: dict | None
) -> AsyncIterator[tuple[Any, str | None, str]]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    shared_connection = (user or {}).get("_scheduled_effect_connection")
    if shared_connection is not None:
        async with shared_connection.transaction():
            await shared_connection.execute(
                SET_SCOPE_SQL, tenant_id or "", workspace_id
            )
            await _assert_scheduled_effect_authority(
                shared_connection, user, tenant_id, workspace_id
            )
            yield shared_connection, tenant_id, workspace_id
        return
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        await _assert_scheduled_effect_authority(conn, user, tenant_id, workspace_id)
        yield conn, tenant_id, workspace_id


async def _assert_scheduled_effect_authority(
    conn: Any,
    user: dict | None,
    tenant_id: str | None,
    workspace_id: str,
) -> None:
    authority = (user or {}).get("_scheduled_effect_authority")
    if authority is None:
        return
    try:
        await conn.execute(
            "SELECT assert_scheduled_effect_authority($1,$2,$3::uuid,$4::uuid,$5::uuid)",
            authority["schedule_run_id"],
            authority["fencing_token"],
            tenant_id,
            workspace_id,
            authority["agent_id"],
        )
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "40001":
            raise HTTPException(409, "scheduled effect authority is stale") from None
        raise


async def run_with_db_scope(
    pool: Any,
    user: dict | None,
    work: Callable[[Any, str | None, str], Awaitable[Any]],
) -> Any:
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        return await work(conn, tenant_id, workspace_id)

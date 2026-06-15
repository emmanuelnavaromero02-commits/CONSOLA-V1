from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import HTTPException


SET_SCOPE_SQL = (
    "SELECT set_config('app.tenant_id', $1, true), "
    "set_config('app.workspace_id', $2, true)"
)


def workspace_scope_from_user(user: dict | None) -> tuple[str | None, str]:
    """Resolve tenant/workspace scope from authenticated runtime context.

    The scope must come from auth/session state. Callers should not pass values
    copied from payloads, query parameters or model/tool arguments.
    """

    data = user or {}
    tenant_id = data.get("active_tenant_id") or data.get("tenant_id")
    workspace_id = data.get("active_workspace_id") or data.get("workspace_id")
    workspace_text = str(workspace_id or "").strip()
    if not workspace_text:
        raise HTTPException(403, "active workspace is required")
    tenant_text = str(tenant_id).strip() if tenant_id else None
    return tenant_text or None, workspace_text


def _looks_like_asyncpg_pool(pool: Any) -> bool:
    return callable(getattr(pool, "acquire", None))


@asynccontextmanager
async def scoped_db(
    pool: Any, tenant_id: str | None, workspace_id: str
) -> AsyncIterator[Any]:
    """Yield a connection with transaction-local Postgres RLS scope set.

    SQL WHERE filters by workspace remain useful defense-in-depth, but scoped
    operational queries must also set the GUCs consumed by RLS policies.
    """

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
async def scoped_db_for_user(pool: Any, user: dict | None) -> AsyncIterator[tuple[Any, str | None, str]]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        yield conn, tenant_id, workspace_id


async def run_with_db_scope(
    pool: Any,
    user: dict | None,
    work: Callable[[Any, str | None, str], Awaitable[Any]],
) -> Any:
    async with scoped_db_for_user(pool, user) as (conn, tenant_id, workspace_id):
        return await work(conn, tenant_id, workspace_id)

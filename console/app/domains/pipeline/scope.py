"""Pipeline run tenant/workspace scoping helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any


BuildSecurityContext = Callable[[dict | None], dict]
TableHasColumn = Callable[..., Awaitable[bool]]
ScopedDbForUser = Callable[..., Any]


async def pipeline_runs_scope_predicate(
    user: dict | None,
    start_index: int = 1,
    *,
    refresh_columns: bool = False,
    build_security_context: BuildSecurityContext,
    table_has_column: TableHasColumn,
) -> tuple[str, list]:
    ctx = build_security_context(user)
    clauses: list[str] = []
    values: list = []
    idx = start_index
    workspace_id = ctx.get("workspace_id")
    tenant_id = ctx.get("tenant_id")
    if workspace_id and await table_has_column(
        "pipeline_runs", "workspace_id", refresh=refresh_columns
    ):
        clauses.append(f"workspace_id=${idx}::uuid")
        values.append(workspace_id)
        idx += 1
    if tenant_id and await table_has_column(
        "pipeline_runs", "tenant_id", refresh=refresh_columns
    ):
        clauses.append(f"tenant_id=${idx}::uuid")
        values.append(tenant_id)
    return (" AND " + " AND ".join(clauses) if clauses else ""), values


@asynccontextmanager
async def pipeline_runs_read_conn(
    pool: Any,
    user: dict | None,
    *,
    build_security_context: BuildSecurityContext,
    scoped_db_for_user: ScopedDbForUser,
):
    """Yield a connection scoped for pipeline_runs RLS when workspace context exists."""
    ctx = build_security_context(user)
    if ctx.get("workspace_id"):
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
            yield conn
        return
    yield pool

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.domains.pipeline.scope import (
    pipeline_runs_read_conn,
    pipeline_runs_scope_predicate,
)


@pytest.mark.asyncio
async def test_pipeline_runs_scope_predicate_uses_workspace_then_tenant():
    calls = []

    async def table_has_column(table: str, column: str, *, refresh: bool = False):
        calls.append((table, column, refresh))
        return True

    sql, values = await pipeline_runs_scope_predicate(
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        3,
        refresh_columns=True,
        build_security_context=lambda user: user or {},
        table_has_column=table_has_column,
    )

    assert sql == " AND workspace_id=$3::uuid AND tenant_id=$4::uuid"
    assert values == ["workspace-a", "tenant-a"]
    assert calls == [
        ("pipeline_runs", "workspace_id", True),
        ("pipeline_runs", "tenant_id", True),
    ]


@pytest.mark.asyncio
async def test_pipeline_runs_scope_predicate_skips_missing_columns():
    async def table_has_column(_table: str, column: str, *, refresh: bool = False):
        return column == "tenant_id"

    sql, values = await pipeline_runs_scope_predicate(
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        build_security_context=lambda user: user or {},
        table_has_column=table_has_column,
    )

    assert sql == " AND tenant_id=$1::uuid"
    assert values == ["tenant-a"]


@pytest.mark.asyncio
async def test_pipeline_runs_read_conn_uses_scoped_connection_for_workspace():
    entered = []

    @asynccontextmanager
    async def scoped_db_for_user(pool, user):
        entered.append((pool, user))
        yield "scoped-conn", "tenant-a", "workspace-a"

    async with pipeline_runs_read_conn(
        "pool",
        {"workspace_id": "workspace-a"},
        build_security_context=lambda user: user or {},
        scoped_db_for_user=scoped_db_for_user,
    ) as conn:
        assert conn == "scoped-conn"

    assert entered == [("pool", {"workspace_id": "workspace-a"})]


@pytest.mark.asyncio
async def test_pipeline_runs_read_conn_uses_pool_without_workspace():
    @asynccontextmanager
    async def scoped_db_for_user(_pool, _user):
        raise AssertionError("scoped db should not be used without workspace")
        yield

    async with pipeline_runs_read_conn(
        "pool",
        {},
        build_security_context=lambda user: user or {},
        scoped_db_for_user=scoped_db_for_user,
    ) as conn:
        assert conn == "pool"

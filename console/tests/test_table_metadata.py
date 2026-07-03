from __future__ import annotations

import pytest

from app.domains.data_platform.table_metadata import (
    clear_table_metadata_cache,
    table_has_column,
)


class _Pool:
    def __init__(self, values: list[bool]):
        self.values = values
        self.calls = 0

    async def fetchval(self, *_args):
        self.calls += 1
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_table_has_column_caches_successful_lookup():
    clear_table_metadata_cache()
    pool = _Pool([True])

    async def pool_getter():
        return pool

    assert await table_has_column(
        "pipeline_runs", "tenant_id", pool_getter=pool_getter
    )
    assert await table_has_column(
        "pipeline_runs", "tenant_id", pool_getter=pool_getter
    )
    assert pool.calls == 1


@pytest.mark.asyncio
async def test_table_has_column_refreshes_cached_lookup():
    clear_table_metadata_cache()
    pool = _Pool([False, True])

    async def pool_getter():
        return pool

    assert not await table_has_column(
        "pipeline_runs", "workspace_id", pool_getter=pool_getter
    )
    assert await table_has_column(
        "pipeline_runs",
        "workspace_id",
        pool_getter=pool_getter,
        refresh=True,
    )
    assert pool.calls == 2

from __future__ import annotations

import pytest

from app.domains.apps.readiness import gold_ready_datasets_for_apps


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class _GoldConn:
    def __init__(self):
        self.closed = False
        self.configured_scope = None

    def transaction(self):
        return _Tx()

    async def execute(self, _sql, tenant_id, workspace_id):
        self.configured_scope = (tenant_id, workspace_id)

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            table_ref = args[0]
            if table_ref in {
                "public.gold_ready_dataset",
                "public.gold_empty_dataset",
                "public.gold_workspace_only_dataset",
            }:
                return table_ref
            return None
        if 'public."gold_ready_dataset"' in sql:
            return 1
        if 'public."gold_workspace_only_dataset"' in sql:
            return 1
        return None

    async def fetch(self, _sql, table):
        if table == "gold_workspace_only_dataset":
            return [{"column_name": "workspace_id"}]
        if table in {"gold_ready_dataset", "gold_empty_dataset"}:
            return [{"column_name": "tenant_id"}, {"column_name": "workspace_id"}]
        return []

    async def close(self):
        self.closed = True


async def _scope(_user):
    return "tenant-1", "workspace-1"


@pytest.mark.asyncio
async def test_gold_ready_datasets_for_apps_checks_scoped_rows():
    conn = _GoldConn()

    async def _connect(_dsn):
        return conn

    ready, mode = await gold_ready_datasets_for_apps(
        {"id": 1},
        {
            "apps": [
                {"datasets_used": ["ready_dataset"]},
                {"datasets_used": ["missing_dataset"]},
                {"datasets_used": ["empty_dataset"]},
                {"datasets_used": ["workspace_only_dataset"]},
            ]
        },
        dsn="postgresql://gold",
        workspace_scope_resolver=_scope,
        connect_gold=_connect,
    )

    assert ready == {"ready_dataset", "workspace_only_dataset"}
    assert mode == "checked"
    assert conn.configured_scope == ("tenant-1", "workspace-1")
    assert conn.closed is True


@pytest.mark.asyncio
async def test_gold_ready_datasets_for_apps_reports_missing_inputs():
    ready, mode = await gold_ready_datasets_for_apps(
        {"id": 1},
        {"apps": [{"name": "shell"}]},
        dsn="postgresql://gold",
        workspace_scope_resolver=_scope,
    )

    assert ready is None
    assert mode == "no_dataset_metadata"

    ready, mode = await gold_ready_datasets_for_apps(
        {"id": 1},
        {"apps": [{"datasets_used": ["ready_dataset"]}]},
        dsn="",
        workspace_scope_resolver=_scope,
    )

    assert ready is None
    assert mode == "gold_dsn_missing"


@pytest.mark.asyncio
async def test_gold_ready_datasets_for_apps_reports_missing_workspace_scope():
    async def _no_scope(_user):
        return "tenant-1", ""

    ready, mode = await gold_ready_datasets_for_apps(
        {"id": 1},
        {"apps": [{"datasets_used": ["ready_dataset"]}]},
        dsn="postgresql://gold",
        workspace_scope_resolver=_no_scope,
    )

    assert ready == set()
    assert mode == "workspace_scope_missing"


@pytest.mark.asyncio
async def test_gold_ready_datasets_for_apps_reports_connect_failure():
    async def _connect(_dsn):
        raise RuntimeError("unreachable")

    ready, mode = await gold_ready_datasets_for_apps(
        {"id": 1},
        {"apps": [{"datasets_used": ["ready_dataset"]}]},
        dsn="postgresql://gold",
        workspace_scope_resolver=_scope,
        connect_gold=_connect,
    )

    assert ready is None
    assert mode == "gold_unreachable"

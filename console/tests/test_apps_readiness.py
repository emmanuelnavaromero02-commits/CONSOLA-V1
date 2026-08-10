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
        self.head_lookups = []

    def transaction(self):
        return _Tx()

    async def execute(self, _sql, tenant_id, workspace_id):
        self.configured_scope = (tenant_id, workspace_id)

    async def fetchrow(self, _sql, tenant_id, workspace_id, dataset):
        self.head_lookups.append((tenant_id, workspace_id, dataset))
        table_by_dataset = {
            "ready_dataset": f"run_{'1' * 32}",
            "empty_dataset": f"run_{'2' * 32}",
            "workspace_only_dataset": f"run_{'3' * 32}",
        }
        table = table_by_dataset.get(dataset)
        if table is None:
            return None
        return {
            "run_id": f"run-id-{dataset}",
            "generation": 1,
            "status": "published",
            "gold_table": table,
            "receipt_id": None,
            "object_checksum": None,
            "evidence_digest": None,
            "object_uri": None,
            "object_version": None,
            "schema_digest": None,
        }

    async def fetchval(self, sql, *args):
        if f'"run_{"1" * 32}"' in sql:
            return 1
        return None

    async def fetch(self, _sql, schema, table):
        assert schema == "omega_publication_gold"
        if table == f"run_{'3' * 32}":
            return [{"column_name": "workspace_id", "data_type": "uuid"}]
        if table in {f"run_{'1' * 32}", f"run_{'2' * 32}"}:
            return [
                {"column_name": "tenant_id", "data_type": "uuid"},
                {"column_name": "workspace_id", "data_type": "uuid"},
            ]
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

    assert ready == {"ready_dataset"}
    assert mode == "checked"
    assert conn.configured_scope == ("tenant-1", "workspace-1")
    assert conn.head_lookups == [
        ("tenant-1", "workspace-1", "empty_dataset"),
        ("tenant-1", "workspace-1", "missing_dataset"),
        ("tenant-1", "workspace-1", "ready_dataset"),
        ("tenant-1", "workspace-1", "workspace_only_dataset"),
    ]
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

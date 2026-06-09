from __future__ import annotations

import pytest

from app.services.intelligence import gold_fetcher


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self):
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self):
        return _Tx()

    async def execute(self, sql: str, *args: object):
        self.execute_calls.append((sql, args))

    async def fetchval(self, sql: str, *args: object):
        return "gold_sap_successfactors_headcount_by_company"

    async def fetch(self, sql: str, *args: object):
        self.fetch_calls.append((sql, args))
        if "information_schema.columns" in sql:
            return [{"column_name": "tenant_id"}, {"column_name": "workspace_id"}, {"column_name": "headcount"}]
        return [{"tenant_id": args[1], "workspace_id": args[0], "headcount": 1288}]

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _clear_gold_cache():
    gold_fetcher._GOLD_ROW_CACHE.clear()
    yield
    gold_fetcher._GOLD_ROW_CACHE.clear()


@pytest.mark.asyncio
async def test_gold_fetcher_scopes_text_or_uuid_gold_columns(monkeypatch):
    conn = _FakeConn()

    async def fake_connect(dsn: str, command_timeout: int):
        assert dsn == "postgresql://gold"
        assert command_timeout == 10
        return conn

    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://gold")
    monkeypatch.setattr(gold_fetcher.asyncpg, "connect", fake_connect)

    rows = await gold_fetcher.query_gold_dataset_rows(
        "sap_successfactors_headcount_by_company",
        {
            "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
            "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
        },
        20,
    )

    data_sql, data_args = conn.fetch_calls[-1]
    assert "workspace_id::text = $1" in data_sql
    assert "tenant_id::text = $2" in data_sql
    assert "::uuid" not in data_sql
    assert data_args == (
        "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
        "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
        20,
    )
    assert rows == [
        {
            "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
            "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
            "headcount": 1288,
        }
    ]


@pytest.mark.asyncio
async def test_gold_fetcher_cache_is_scoped_by_workspace(monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://gold")
    monkeypatch.setenv("OMEGA_GOLD_ROW_CACHE_TTL_SECONDS", "60")
    connects: list[_FakeConn] = []

    async def fake_connect(_dsn: str, command_timeout: int):
        conn = _FakeConn()
        connects.append(conn)
        return conn

    monkeypatch.setattr(gold_fetcher.asyncpg, "connect", fake_connect)

    base_user = {
        "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
        "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    }
    first = await gold_fetcher.query_gold_dataset_rows("sap_successfactors_employee_360", base_user, 20)
    second = await gold_fetcher.query_gold_dataset_rows("sap_successfactors_employee_360", base_user, 20)
    other = await gold_fetcher.query_gold_dataset_rows(
        "sap_successfactors_employee_360",
        {**base_user, "workspace_id": "00000000-0000-0000-0000-000000000002"},
        20,
    )

    assert first == second
    assert other != first
    assert len(connects) == 2

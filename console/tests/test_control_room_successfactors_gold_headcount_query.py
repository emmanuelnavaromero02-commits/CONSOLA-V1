from __future__ import annotations

from typing import Any

import pytest

from app.services import control_room_service
from app.services.intelligence import (
    gold_fetcher,
    successfactors_gold_headcount as headcount_query,
)


USER = {
    "tenant_id": "tenant-a",
    "active_tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "active_workspace_id": "workspace-a",
}
_BLANK_FILLERS = (
    "\u115f\u1160\u2800\u3164\ua8f9\uffa0"
    "\U00010af6\U0001144e\U00011945\U00011c44\U00011c45"
    "\U00011f48\U00013441\U00013442\U00016fe4"
)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, *, omit_company: bool = False, invalid_company: bool = False):
        self.omit_company = omit_company
        self.invalid_company = invalid_company
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.cursor_calls: list[tuple[str, tuple[object, ...], int]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.transaction_calls: list[dict[str, object]] = []

    def transaction(self, **kwargs: object):
        self.transaction_calls.append(kwargs)
        return _Transaction()

    async def execute(self, sql: str, *args: object):
        self.execute_calls.append((sql, args))

    async def fetch(self, sql: str, *args: object):
        self.fetch_calls.append((sql, args))
        if "information_schema.columns" in sql:
            rows = []
            for _key, dataset, name_key in headcount_query._DIMENSIONS:
                if self.omit_company and dataset.endswith("_company"):
                    continue
                table = gold_fetcher._gold_table(dataset)
                rows.extend(
                    {"table_name": table, "column_name": column, "data_type": data_type}
                    for column, data_type in (
                        ("tenant_id", "text"),
                        ("workspace_id", "text"),
                        (name_key, "text"),
                        ("headcount", "bigint"),
                    )
                )
            return rows
        if "WITH scoped AS MATERIALIZED" in sql:
            return self._aggregate_rows(sql)
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchrow(self, _sql: str, *args: object):
        dataset = str(args[2])
        if self.omit_company and dataset.endswith("_company"):
            return None
        return {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "generation": 1,
            "status": "legacy_unverified",
            "gold_table": gold_fetcher._gold_table(dataset),
            "receipt_id": None,
            "object_checksum": None,
            "evidence_digest": None,
            "object_uri": None,
            "object_version": None,
            "schema_digest": None,
        }

    def cursor(self, sql: str, *args: object, prefetch: int):
        self.cursor_calls.append((sql, args, prefetch))

        async def rows():
            for row in self._label_rows(sql):
                yield row

        return rows()

    def _label_rows(self, sql: str) -> list[dict[str, object]]:
        if "headcount_by_company" in sql:
            return [
                *(
                    {"business_name": f"Compania {index:04d}"}
                    for index in range(1, 1002)
                ),
                {"business_name": "\u2003"},
                {"business_name": "（ｓｉｎ　ｎｏｍｂｒｅ）"},
                *(
                    {"business_name": value}
                    for value in (
                        "\u200b",
                        "\u2060",
                        "\ufeff",
                        "\u202aNombre",
                        "Nombre\u202e",
                        "\u2066Nombre\u2069",
                        "Nombre\ufe0f",
                        "Nombre\U000e0100",
                        "\u034f",
                        "\u2028",
                        "\u2029",
                        *_BLANK_FILLERS,
                        *(f"Nombre{filler}" for filler in _BLANK_FILLERS),
                        "\u200b\u2060\ufeff",
                    )
                ),
            ]
        if "headcount_by_department" in sql:
            return [{"business_name": " Personas "}]
        return [{"business_name": value} for value in _BLANK_FILLERS]

    def _aggregate_rows(self, sql: str) -> list[dict[str, Any]]:
        if "headcount_by_company" in sql:
            invalid = 1 if self.invalid_company else 0
            return [
                {
                    "invalid_count": invalid,
                    "valid_count": 1001,
                    "total_headcount": 501_501,
                    "business_name": f"Compania {value:04d}",
                    "headcount": value,
                }
                for value in range(1001, 996, -1)
            ]
        return [
            {
                "invalid_count": 0,
                "valid_count": 1,
                "total_headcount": 0,
                "business_name": " Personas ",
                "headcount": 0,
            }
        ]

    async def close(self):
        return None


async def _run(monkeypatch, conn: _FakeConnection):
    async def connect(dsn: str, command_timeout: int):
        assert dsn == "postgresql://gold"
        assert command_timeout == 10
        return conn

    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://gold")
    monkeypatch.setattr(headcount_query.asyncpg, "connect", connect)
    return await headcount_query.query_successfactors_headcount_summaries(USER)


@pytest.mark.asyncio
async def test_scoped_query_aggregates_over_1000_before_global_top_limit(monkeypatch):
    conn = _FakeConnection()

    results = await _run(monkeypatch, conn)

    company = results["sap_successfactors_headcount_by_company"]
    assert company["total"] == 501_501
    assert [row["headcount"] for row in company["rows"]] == [1001, 1000, 999, 998, 997]
    assert results["sap_successfactors_headcount_by_location"] == {
        "rows": [],
        "total": None,
        "status": "empty",
        "error": None,
    }
    assert results["sap_successfactors_headcount_by_department"]["rows"] == [
        {"department_name": "Personas", "headcount": 0}
    ]

    label_sql, label_args, prefetch = next(
        call for call in conn.cursor_calls if "headcount_by_company" in call[0]
    )
    assert "LIMIT" not in label_sql
    assert label_args == ("workspace-a", "tenant-a")
    assert prefetch == 1_000
    aggregate_sql, aggregate_args = next(
        (sql, args)
        for sql, args in conn.fetch_calls
        if "headcount_by_company" in sql and "WITH scoped" in sql
    )
    accepted = aggregate_args[2]
    assert len(accepted) == 1001
    assert "\u2003" not in accepted
    assert "（ｓｉｎ　ｎｏｍｂｒｅ）" not in accepted
    assert "SUM(headcount)" in aggregate_sql
    assert aggregate_sql.index("ORDER BY headcount DESC") < aggregate_sql.index(
        "LIMIT $4"
    )
    assert "workspace_id::text = $1" in aggregate_sql
    assert "tenant_id::text = $2" in aggregate_sql
    assert aggregate_args[3] == 5
    assert conn.transaction_calls[0] == {
        "isolation": "repeatable_read",
        "readonly": True,
    }
    assert conn.execute_calls[0][1] == ("tenant-a", "workspace-a")


@pytest.mark.asyncio
async def test_dimension_failure_is_isolated_and_malformed_counts_fail_closed(
    monkeypatch,
):
    missing = await _run(monkeypatch, _FakeConnection(omit_company=True))
    assert missing["sap_successfactors_headcount_by_company"]["status"] == "missing"
    assert missing["sap_successfactors_headcount_by_department"]["status"] == "ready"

    invalid = await _run(monkeypatch, _FakeConnection(invalid_company=True))
    assert invalid["sap_successfactors_headcount_by_company"]["status"] == "unavailable"
    assert invalid["sap_successfactors_headcount_by_company"]["total"] is None
    assert invalid["sap_successfactors_headcount_by_department"]["status"] == "ready"


@pytest.mark.asyncio
async def test_gold_service_preserves_full_total_not_only_five_visible_rows(
    monkeypatch,
):
    async def employee_rows(_dataset: str, _user: dict | None, _limit: int):
        return []

    async def summaries(_user: dict | None, *, limit: int):
        empty = {"rows": [], "total": None, "status": "empty", "error": None}
        return {
            "sap_successfactors_employee_360": {
                "rows": [],
                "total": 501_501,
                "status": "ready",
                "error": None,
            },
            "sap_successfactors_headcount_by_company": {
                "rows": [
                    {"company_name": f"Compania {value}", "headcount": value}
                    for value in range(1001, 996, -1)
                ],
                "total": 501_501,
                "status": "ready",
                "error": None,
            },
            "sap_successfactors_headcount_by_location": dict(empty),
            "sap_successfactors_headcount_by_department": dict(empty),
        }

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", employee_rows)
    monkeypatch.setattr(
        headcount_query, "query_successfactors_headcount_summaries", summaries
    )
    payload = await control_room_service.sap_successfactors_gold_kpis(USER)
    company = next(
        widget
        for widget in payload["widgets"]
        if widget["id"] == "sf_headcount_by_company"
    )

    assert company["value"] == 501_501
    assert [row["headcount"] for row in company["rows"]] == [1001, 1000, 999, 998, 997]

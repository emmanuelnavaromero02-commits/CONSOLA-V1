from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
    "allowed_cartridges": ["sap_hcm"],
}


class MutationSentinelPool:
    def __init__(self, *, allow_insert: bool = False) -> None:
        self.allow_insert = allow_insert
        self.mutation_attempts: list[str] = []
        self.persisted_rows = [
            {
                "item_id": "historical-source-state",
                "item_kind": "source_state",
                "last_seen_at": datetime(2026, 7, 1, tzinfo=UTC),
            }
        ]

    @staticmethod
    def _statement(query: str) -> str:
        return " ".join(str(query).split()).upper()

    async def execute(self, query: str, *args):
        statement = self._statement(query)
        if statement.startswith("SELECT SET_CONFIG"):
            return None
        if statement.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            self.mutation_attempts.append(statement)
            if self.allow_insert and statement.startswith("INSERT "):
                payload = json.loads(args[0]) if args else []
                return f"INSERT 0 {len(payload)}"
            raise AssertionError(f"DML attempted from read path: {statement[:80]}")
        raise AssertionError(f"unexpected execute: {statement[:80]}")

    async def fetch(self, query: str, *_args):
        statement = self._statement(query)
        assert not statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        return []

    async def fetchrow(self, query: str, *_args):
        statement = self._statement(query)
        assert not statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        return None

    async def fetchval(self, query: str, *_args):
        statement = self._statement(query)
        assert not statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        return 0

    def add(self, *_args):
        self.mutation_attempts.append("ADD")
        raise AssertionError("ORM add attempted from read path")

    def delete(self, *_args):
        self.mutation_attempts.append("DELETE")
        raise AssertionError("ORM delete attempted from read path")

    async def flush(self):
        self.mutation_attempts.append("FLUSH")
        raise AssertionError("ORM flush attempted from read path")

    async def commit(self):
        self.mutation_attempts.append("COMMIT")
        raise AssertionError("commit attempted from read path")


async def _fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    if dataset == "employees_anomalies":
        return [
            {
                "tenant_id": "tenant-A",
                "workspace_id": "workspace-A",
                "pernr": "1001",
                "full_name": "Ana Gomez",
                "anomaly_type": "terminated_but_active",
                "severity": "critical",
                "details": {"salary_monthly_usd": 4200},
                "detected_at": "2026-07-16T10:00:00Z",
            }
        ]
    return []


async def _installations(_user):
    return [
        {
            "cartridge_id": "sap_hcm",
            "installation_status": "ready",
            "connection_id": "sap_hcm_test",
            "auth_method": "test",
        }
    ]


@pytest.mark.asyncio
async def test_all_mutating_get_surfaces_are_read_only_and_repeatable():
    pool = MutationSentinelPool()
    before = deepcopy(pool.persisted_rows)
    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=pool)
        ),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(side_effect=_installations),
        ),
    ):
        first = await control_room_service.dashboard(USER, fetcher=_fetcher)
        second = await control_room_service.dashboard(USER, fetcher=_fetcher)
        item = first["items"][0]
        item_id = item["id"]

        alerts = await control_room_service.list_alerts(USER, fetcher=_fetcher)
        detail = await control_room_service.get_item(item_id, USER, fetcher=_fetcher)
        anomaly = await control_room_service.get_anomaly(
            item_id, USER, fetcher=_fetcher
        )
        impact = await control_room_service.get_item_impact(
            item_id, USER, fetcher=_fetcher
        )
        activity = await control_room_service.get_item_activity(
            item_id, USER, fetcher=_fetcher
        )
        runs = await control_room_service.list_item_action_runs(
            item_id, USER, fetcher=_fetcher
        )
        outcomes = await control_room_service.list_item_outcomes(
            item_id, USER, fetcher=_fetcher
        )

    assert [row["id"] for row in first["items"]] == [
        row["id"] for row in second["items"]
    ]
    assert detail["id"] == anomaly["id"] == impact["item_id"] == item_id
    assert alerts["summary"]["total"] >= 1
    assert activity["item_id"] == runs["item_id"] == outcomes["item_id"] == item_id
    assert pool.persisted_rows == before
    assert pool.mutation_attempts == []


@pytest.mark.asyncio
async def test_explicit_refresh_is_the_only_dashboard_state_writer():
    pool = MutationSentinelPool(allow_insert=True)
    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=pool)
        ),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(side_effect=_installations),
        ),
    ):
        payload = await control_room_service.refresh_dashboard_state(
            USER,
            fetcher=_fetcher,
        )

    assert payload["items"]
    assert pool.mutation_attempts
    assert all(statement.startswith("INSERT ") for statement in pool.mutation_attempts)
    assert any(
        "INSERT INTO CONTROL_ROOM_ITEMS" in statement
        for statement in pool.mutation_attempts
    )

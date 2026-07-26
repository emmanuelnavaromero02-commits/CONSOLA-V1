"""Per-recommendation RBAC for persisted Copilot context."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import copilot_context_service as service


TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
VIEWER = {
    "id": 7,
    "role": "viewer",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
}
OPERATOR = {**VIEWER, "id": 8, "role": "tenant_admin"}


def _row(fingerprint: str, required_permission: str) -> dict[str, object]:
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "fingerprint": fingerprint,
        "title": "Revisar señal",
        "required_permission": required_permission,
        "status": "active",
    }


def _install_db(monkeypatch, connection) -> None:
    monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "_tables_ready", AsyncMock(return_value=True))

    @asynccontextmanager
    async def scoped_db(*_args, **_kwargs):
        yield connection

    monkeypatch.setattr(service, "scoped_db", scoped_db)


@pytest.mark.asyncio
async def test_list_filters_each_recommendation_by_declared_permission(
    monkeypatch,
) -> None:
    class Connection:
        async def fetch(self, sql: str, *args):
            assert "required_permission = ANY($3::text[])" in sql
            assert "LIMIT $4" in sql
            assert args[3] == 20
            return [
                _row("live:monitor", "monitor.read"),
                _row("live:operations", "operations.read"),
                _row("live:unknown", "unknown.permission"),
            ]

    _install_db(monkeypatch, Connection())

    viewer = await service.list_recommendations(VIEWER)
    operator = await service.list_recommendations(OPERATOR)

    assert [row["fingerprint"] for row in viewer["recommendations"]] == [
        "live:monitor"
    ]
    assert [row["fingerprint"] for row in operator["recommendations"]] == [
        "live:monitor",
        "live:operations",
    ]


@pytest.mark.asyncio
async def test_dismiss_applies_the_same_permission_inside_atomic_update(
    monkeypatch,
) -> None:
    class Connection:
        mutated = False
        calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetchrow(self, sql: str, *args):
            self.calls.append((sql, args))
            if "operations.read" not in args[3]:
                return None
            self.mutated = True
            return {
                "id": "33333333-3333-3333-3333-333333333333",
                "fingerprint": "live:operations",
                "status": "dismissed",
            }

    connection = Connection()
    _install_db(monkeypatch, connection)

    with pytest.raises(HTTPException) as denied:
        await service.dismiss_recommendation(VIEWER, "live:operations")
    assert denied.value.status_code == 404
    assert connection.mutated is False

    result = await service.dismiss_recommendation(OPERATOR, "live:operations")

    assert result["status"] == "dismissed"
    assert connection.mutated is True
    sql, args = connection.calls[-1]
    assert "required_permission = ANY($4::text[])" in sql
    assert "operations.read" in args[3]


def test_talent_metadata_recommendation_requires_operations_read() -> None:
    recommendations = service.build_recommendations_from_snapshot(
        {
            "sources": [
                {
                    "name": "control_room.sap_successfactors_talent_metadata_readiness",
                    "data": {
                        "status": "partial",
                        "summary": {
                            "blocked_entities": 1,
                            "live_required_total": 2,
                            "live_required_ready": 1,
                        },
                    },
                }
            ]
        }
    )

    metadata = next(
        item for item in recommendations if "metadata" in item["title"].lower()
    )
    assert metadata["required_permission"] == "operations.read"

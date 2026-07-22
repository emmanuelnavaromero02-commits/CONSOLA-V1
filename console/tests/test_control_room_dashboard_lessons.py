from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_source_scope import scoped_source_row


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
    "_effective_permissions": [
        "control_room.read",
        "control_room.write",
        "control_room.execute",
    ],
}

SAMPLE_ROWS = {
    "employees_anomalies": [
        {
            "pernr": "1001",
            "full_name": "Ana Gomez",
            "anomaly_type": "terminated_but_active",
            "severity": "critical",
            "details": '{"reason":"Baja terminada pero usuario activo"}',
            "detected_at": "2026-05-20T10:00:00Z",
        }
    ],
}

for _source in control_room_service._all_sources():  # noqa: SLF001
    SAMPLE_ROWS.setdefault(_source.dataset, [])


@pytest.fixture(autouse=True)
def scoped_test_installations(monkeypatch):
    async def installed(user):
        allowed = {
            str(item)
            for item in (user or {}).get("allowed_cartridges", [])
            if str(item).strip()
        }
        return [
            {
                "cartridge_id": cartridge_id,
                "installation_status": "ready",
                "connection_id": f"{cartridge_id}_test",
                "auth_method": "test",
            }
            for cartridge_id in sorted(allowed)
        ]

    monkeypatch.setattr(control_room_service, "_installed_cartridges", installed)


async def sample_fetcher(dataset: str, user: dict | None, _limit: int) -> list[dict]:
    context = user or USER
    source = next(
        (
            item
            for item in control_room_service._all_sources()  # noqa: SLF001
            if item.dataset == dataset
        ),
        None,
    )
    scoped = []
    for row in SAMPLE_ROWS[dataset]:
        source_row = {
            **row,
            "tenant_id": context["tenant_id"],
            "workspace_id": context["active_workspace_id"],
        }
        projected = scoped_source_row(
            source_row,
            tenant_id=context["tenant_id"],
            workspace_id=context["active_workspace_id"],
        )
        observed_at = str(source_row.get("detected_at") or "")
        if source is not None and observed_at:
            projected.update(
                runtime_row_evidence_fields(
                    source_dataset=dataset,
                    source_system=source.cartridge,
                    cartridge=source.cartridge,
                    tenant_id=context["tenant_id"],
                    workspace_id=context["active_workspace_id"],
                    source_row=source_row,
                    locator_field=source.entity_id_field,
                    observed_at=observed_at,
                    business_observation=projected,
                )
            )
        scoped.append(projected)
    return scoped


@pytest.mark.asyncio
async def test_dashboard_does_not_attach_lessons_from_historical_ordinary_parent():
    lesson_row = {
        "id": 31,
        "item_id": "historic-item",
        "cartridge_id": "sap_hcm",
        "anomaly_type": "terminated_but_active",
        "rule": "Cuando un empleado terminado sigue activo, bloquear acceso antes del cierre de nomina.",
        "source_decision_id": 42,
        "confidence": 0.86,
        "metadata": {"source_dataset": "employees_anomalies"},
        "created_at": datetime(2026, 5, 21, 9, 30, 0),
    }
    persisted_parent = {
        "id": "historic-item",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "module": "SAP HCM",
        "domain": "Recursos Humanos",
        "source_dataset": "employees_anomalies",
        "anomaly_type": "terminated_but_active",
        "severity": "high",
        "severity_weight": 3,
        "entity_kind": "Empleado",
        "entity_id": "historic-employee",
        "entity_label": "Historic employee",
        "title": "Historic terminated employee",
        "description": "Historic business anomaly",
        "recommendation": "Review",
        "detected_at": "2026-05-21T09:30:00Z",
        "status": "resolved",
    }
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_hcm",
                        "installation_status": "ready",
                        "label": "SAP HCM",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(return_value=[persisted_parent]),
        ),
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=[lesson_row]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    item = next(
        item
        for item in result["items"]
        if item["anomaly_type"] == "terminated_but_active"
    )
    assert item["lesson_count"] == 0
    assert item["related_lessons"] == []
    assert all(
        "bloquear acceso" not in rule for rule in item["omega"]["lessons"]["rules"]
    )
    assert result["summary"]["lessons"]["total"] == 0


@pytest.mark.asyncio
async def test_get_item_activity_propagates_database_failures():
    item = {"id": "item-activity", "decision_id": None}
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=RuntimeError("activity lookup down"))

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_item_for_read",
            new=AsyncMock(return_value=item),
        ),
    ):
        with pytest.raises(RuntimeError, match="activity lookup down"):
            await control_room_service.get_item_activity("item-activity", USER)

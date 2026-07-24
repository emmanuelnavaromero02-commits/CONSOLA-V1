from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from console.tests.control_room_execution_helpers import observed_anomaly_fields
from console.tests.test_control_room_service import USER


@pytest.mark.asyncio
async def test_dashboard_filters_persisted_intelligence_to_active_connections(
    monkeypatch,
):
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        return []

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    stale_item = {
        **observed_anomaly_fields("alert:hubspot:stale"),
        "id": "alert:hubspot:stale",
        "kind": "intelligence_signal",
        "domain": "Ventas",
        "module": "HubSpot",
        "module_id": "hubspot",
        "cartridge": "hubspot",
        "source_dataset": "hubspot_deals",
        "evidence_refs": ["hubspot_deals:alert:hubspot:stale"],
        "entity_kind": "Deal",
        "entity_id": "D-1",
        "entity_label": "Deal",
        "anomaly_type": "forecast",
        "severity": "high",
        "severity_weight": 3,
        "title": "HubSpot stale alert",
        "description": "Should be hidden without scoped connection.",
        "recommendation": "Hidden",
        "status": "open",
    }
    active_item = {
        **stale_item,
        "id": "alert:sf:active",
        "domain": "Recursos Humanos",
        "module": "Employee Central",
        "module_id": "sap_successfactors",
        "cartridge": "sap_successfactors",
        "source_dataset": "sap_successfactors_employee_360",
        "evidence_refs": ["sap_successfactors_employee_360:alert:sf:active"],
        "title": "SuccessFactors active alert",
    }

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_load_lesson_rows", new=AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(
                return_value=[
                    {
                        "cartridge_id": "sap_successfactors",
                        "installation_status": "ready",
                        "connection_id": "femsa_sf",
                        "auth_method": "saml_bearer_assertion",
                    },
                ]
            ),
        ),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(return_value=[stale_item, active_item]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=fetcher)

    assert {item["cartridge"] for item in result["items"]} == {"sap_successfactors"}
    assert {alert["cartridge"] for alert in result["alerts"]} == {"sap_successfactors"}

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
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
    "business_partner_anomalies": [
        {
            "business_partner": "BP-9",
            "full_name": "Northwind",
            "anomaly_type": "missing_address",
            "severity": "high",
            "details": {"reason": "Sin direccion fiscal"},
            "detected_at": "2026-05-20T11:00:00Z",
        }
    ],
    "sap_successfactors_employees_anomalies": [
        {
            "user_id": "sf-77",
            "full_name": "Luis Perez",
            "anomaly_type": "missing_manager",
            "severity": "medium",
            "details": "{}",
            "detected_at": "2026-05-20T12:00:00Z",
        }
    ],
    "consultor_asignacion": [],
    "consultor_timesheet_semanal": [],
    "pnl_mensual": [],
    "analytic_skill_gap_by_manager": [],
}


async def sample_fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
    return SAMPLE_ROWS[dataset]


@pytest.mark.asyncio
async def test_list_anomalies_normalizes_all_real_sources():
    result = await control_room_service.list_anomalies(USER, fetcher=sample_fetcher)

    assert len(result["anomalies"]) == 3
    assert {item["source_dataset"] for item in result["anomalies"]} == {
        "employees_anomalies",
        "business_partner_anomalies",
        "sap_successfactors_employees_anomalies",
    }
    first = result["anomalies"][0]
    assert first["source_dataset"] == "employees_anomalies"
    assert first["severity"] == "critical"
    assert first["severity_weight"] == 4
    assert first["recommendation"]
    assert first["id"]
    source_summary = {
        source["dataset"]: {key: source[key] for key in ("cartridge", "status", "count")}
        for source in result["sources"]
    }
    assert source_summary["employees_anomalies"] == {"cartridge": "sap_hcm", "status": "ok", "count": 1}
    assert source_summary["business_partner_anomalies"] == {
        "cartridge": "sap_s4hana",
        "status": "ok",
        "count": 1,
    }
    assert source_summary["sap_successfactors_employees_anomalies"] == {
        "cartridge": "sap_successfactors",
        "status": "ok",
        "count": 1,
    }
    assert {item["dataset"] for item in result["sources"][3:]} == {
        "consultor_asignacion",
        "consultor_timesheet_semanal",
        "pnl_mensual",
        "analytic_skill_gap_by_manager",
    }
    assert all(item["status"] == "empty" for item in result["sources"][3:])


@pytest.mark.asyncio
async def test_list_anomalies_marks_missing_dataset_unavailable_without_failing():
    async def fetcher(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "business_partner_anomalies":
            raise HTTPException(404, "dataset not found")
        return SAMPLE_ROWS[dataset]

    result = await control_room_service.list_anomalies(USER, fetcher=fetcher)

    assert len(result["anomalies"]) == 2
    failed_source = next(item for item in result["sources"] if item["dataset"] == "business_partner_anomalies")
    assert failed_source["status"] == "missing"
    assert failed_source["count"] == 0
    assert "dataset not found" in failed_source["error"]


@pytest.mark.asyncio
async def test_dashboard_keeps_active_empty_cartridges_visible_and_creates_source_items():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "replicon", "installation_status": "ready", "label": "Replicon"},
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=sample_fetcher)

    cartridge_ids = {item["id"] for item in result["cartridges"]}
    assert {"sap_hcm", "replicon"} <= cartridge_ids
    replicon = next(item for item in result["cartridges"] if item["id"] == "replicon")
    assert replicon["source_status"] == "empty"
    assert any(item["kind"] == "source_state" and item["cartridge"] == "replicon" for item in result["items"])
    assert result["omega_steps"][0]["label"] == "Senales"


@pytest.mark.asyncio
async def test_summary_counts_and_scopes_open_decisions_to_active_workspace():
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = 5

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        result = await control_room_service.summary(USER, fetcher=sample_fetcher)

    assert result["total_anomalies"] == 3
    assert result["by_severity"] == {"critical": 1, "high": 1, "medium": 1, "low": 0}
    assert result["by_cartridge"] == {"sap_hcm": 1, "sap_s4hana": 1, "sap_successfactors": 1}
    assert result["open_decisions"] == 5
    sql, workspace_id = mock_pool.fetchval.call_args[0]
    assert "workspace_id = $1" in sql
    assert workspace_id == "workspace-A"


@pytest.mark.asyncio
async def test_create_decision_writes_workspace_bitacora_and_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    decision_row = {
        "id": 42,
        "title": "Decision title",
        "workspace_id": "workspace-A",
        "created_at": datetime(2026, 5, 20, 10, 0, 0),
    }
    action_row = {"id": 99, "decision_id": 42}
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=[decision_row, action_row])
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "label": "SuccessFactors",
                },
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.create_decision_for_anomaly(
            anomaly["id"],
            USER,
            fetcher=sample_fetcher,
        )

    assert result["decision"]["id"] == 42
    assert mock_pool.fetchrow.call_count == 2
    insert_sql = mock_pool.fetchrow.call_args_list[0].args[0]
    assert "INSERT INTO decisions" in insert_sql
    assert "workspace_id" in insert_sql
    assert mock_pool.fetchrow.call_args_list[0].args[-1] == "workspace-A"
    action_sql = mock_pool.fetchrow.call_args_list[1].args[0]
    assert "INSERT INTO decision_actions" in action_sql
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.decision.create"
    assert audit_event.await_args.kwargs["metadata"]["decision_id"] == 42
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_approve_anomaly_requires_workspace_decision_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            {"id": 42},
            {
                "id": 100,
                "decision_id": 42,
                "action_text": "approved",
                "note": "ok",
                "actor": "ops@example.com",
                "ts": datetime(2026, 5, 20, 10, 1, 0),
            },
        ]
    )
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
                {"cartridge_id": "sap_s4hana", "installation_status": "ready", "label": "SAP S/4HANA"},
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "label": "SuccessFactors",
                },
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.approve_anomaly(
            anomaly["id"],
            USER,
            decision_id=42,
            fetcher=sample_fetcher,
        )

    assert result["approved"] is True
    assert result["decision_id"] == 42
    assert result["action"]["ts"] == "2026-05-20T10:01:00"
    visible_sql, decision_id, workspace_id = mock_pool.fetchrow.call_args_list[0].args
    assert "workspace_id = $2" in visible_sql
    assert decision_id == 42
    assert workspace_id == "workspace-A"
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.approve"
    assert audit_event.await_args.kwargs["resource_type"] == "control_room_item"


@pytest.mark.asyncio
async def test_dismiss_item_persists_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.dismiss_item(
            anomaly["id"],
            USER,
            reason="false positive",
            fetcher=sample_fetcher,
        )

    assert result["dismissed"] is True
    assert result["item"]["status"] == "dismissed"
    assert any("UPDATE control_room_items" in call.args[0] for call in mock_pool.execute.call_args_list)
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.dismiss"


@pytest.mark.asyncio
async def test_reopen_item_resets_terminal_state_and_records_audit_event():
    anomaly = (await control_room_service.list_anomalies(USER, fetcher=sample_fetcher))["anomalies"][0]
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {"cartridge_id": "sap_hcm", "installation_status": "ready", "label": "SAP HCM"},
            ]),
        ),
        patch.object(control_room_service.audit_service, "record_event", new=AsyncMock()) as audit_event,
    ):
        result = await control_room_service.reopen_item(
            anomaly["id"],
            USER,
            reason="e2e reset",
            fetcher=sample_fetcher,
        )

    assert result["reopened"] is True
    assert result["item"]["status"] == "open"
    assert result["item"]["decision_id"] is None
    assert any("UPDATE control_room_items" in call.args[0] for call in mock_pool.execute.call_args_list)
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.reopen"

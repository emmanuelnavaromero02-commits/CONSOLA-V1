from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.schemas import control_room_legacy_responses as responses
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, OPERATOR, client


def test_summary_restores_business_kpis_without_source_diagnostics_or_ids():
    projected = responses.ControlRoomBusinessSummaryResponse.project(
        {
            "total_anomalies": 3,
            "by_severity": {"critical": 0, "high": 2, "medium": 1},
            "by_cartridge": {"sap_hcm": 1, "sap_successfactors": 2, "unknown_id": 9},
            "by_domain": {"Recursos Humanos": 2, "Finanzas": 1, "internal": 9},
            "open_decisions": 0,
            "sources": [
                {
                    "cartridge": "sap_hcm",
                    "count": 0,
                    "status": "unavailable",
                    "data_readiness": "blocked",
                    "dataset": "private_table",
                },
                {"cartridge": "sap_successfactors", "count": 12},
            ],
            "financial": {
                "status": "ok",
                "sources": {"private_table": {"status": "ok"}},
                "revenue_usd": 0,
                "billed_usd": 12,
                "margin_pct": None,
                "backlog_value": 30,
                "oldest_backlog_days": 0,
                "risk_projects": [
                    {
                        "label": "Proyecto Norte",
                        "owner": "owner-technical-id",
                        "margin_pct": 0,
                        "wip_usd": 0,
                        "margin_usd": 4,
                    }
                ],
            },
            "thresholds": {"active": 0, "total": 2, "by_cartridge": {"sap_hcm": 2}},
            "lessons": {
                "total": 1,
                "recent": [
                    {
                        "id": "lesson-technical-id",
                        "rule": "Mantener revision humana",
                        "confidence": 0,
                        "created_at": "2026-07-26T10:00:00Z",
                    }
                ],
                "by_cartridge": {"sap_hcm": 1},
                "top_patterns": [
                    {
                        "cartridge_id": "sap_hcm",
                        "anomaly_type": "raw_internal_code",
                        "count": 1,
                        "avg_confidence": 0,
                        "latest_rule": "Mantener revision humana",
                        "last_seen_at": "2026-07-26T10:00:00Z",
                    }
                ],
            },
            "alerts": {"total": 0, "open": 0},
        }
    ).model_dump()

    assert projected["by_cartridge"] == [
        {"label": "Personal y nomina", "count": 1},
        {"label": "Talento y organizacion", "count": 2},
    ]
    assert projected["by_domain"] == [
        {"label": "Finanzas", "count": 1},
        {"label": "Recursos Humanos", "count": 2},
    ]
    assert projected["sources"] == [
        {"label": "Personal y nomina", "count": 0},
        {"label": "Talento y organizacion", "count": 12},
    ]
    assert projected["financial"]["revenue_usd"] == 0
    assert projected["financial"]["margin_pct"] is None
    assert projected["financial"]["risk_projects"][0]["margin_pct"] == 0
    assert projected["thresholds"] == {"active": 0, "total": 2}
    assert projected["lessons"]["recent"][0]["rule"] == "Mantener revision humana"
    assert projected["lessons"]["by_capability"][0]["label"] == "Personal y nomina"
    assert "source_state" not in str(projected)
    assert "data_readiness" not in str(projected)
    assert "private_table" not in str(projected)
    assert "technical-id" not in str(projected)
    assert "raw_internal_code" not in str(projected)


def test_gold_projection_keeps_only_real_business_names():
    projected = responses.ControlRoomGoldKpisResponse.project(
        {
            "widgets": [
                {
                    "id": "sf_headcount",
                    "rows": [
                        {"company_name": "FEMSA Comercio", "headcount": 2},
                        {"location_name": "Monterrey", "headcount": 1},
                        {"department_name": "Finanzas", "headcount": 0},
                    ],
                }
            ]
        }
    ).model_dump()

    rows = projected["widgets"][0]["rows"]
    assert rows[0]["company_name"] == "FEMSA Comercio"
    assert rows[1]["location_name"] == "Monterrey"
    assert rows[2]["department_name"] == "Finanzas"


def test_gold_service_does_not_fabricate_or_fallback_to_technical_id():
    rows = control_room_service._sf_gold_top_headcount_rows(
        [
            {
                "company_id": "technical-company-id",
                "company_name": "Comercio",
                "headcount": 2,
            },
            {"company_id": "fallback-must-not-appear", "headcount": 1},
        ],
        ("company_id", "company_name"),
    )

    assert rows[0]["company_name"] == rows[0]["label"] == "Comercio"
    assert rows[1]["company_name"] is None
    assert rows[1]["label"] is None
    assert "technical-company-id" not in str(rows)
    assert "fallback-must-not-appear" not in str(rows)


def test_operator_metadata_contract_uses_business_components_and_redacts():
    projected = responses.ControlRoomTalentMetadataReadinessResponse.project(
        {
            "generated_at": "2026-07-26T10:00:00Z",
            "status": "partial",
            "tenant_id": "private-tenant",
            "summary": {
                "cpa_ready_employees": 0,
                "cpa_insufficient_employees": 2,
                "entities": 6,
                "blocked_entities": 2,
                "live_required_ready": 1,
                "live_required_total": 3,
                "live_status": "partial",
            },
            "entities": [
                {
                    "id": "performance",
                    "entity": "FormHeader/PerformanceReview",
                    "required_for": "performance_score",
                    "status": "available",
                    "ready_to_extract": False,
                },
                {"id": "connection-secret", "status": "ready"},
            ],
            "blockers": [
                {
                    "id": "technical-id",
                    "status": "blocked",
                    "title": "Permiso pendiente",
                    "detail": "password=TOPSECRET",
                }
            ],
            "live_preflight": {"status": "partial", "connection_id": "secret"},
        }
    ).model_dump()

    assert projected["components"] == [
        {
            "component": "Desempeno",
            "purpose": "Evaluacion de desempeno",
            "status": "available",
            "ready_to_extract": False,
        }
    ]
    assert projected["summary"]["blocked_components"] == 2
    assert projected["source_check"] == {
        "status": "partial",
        "required_ready": 1,
        "required_total": 3,
    }
    assert projected["blockers"][0]["detail"] == "[REDACTED]"
    assert not {"entities", "live_preflight", "tenant_id"} & set(projected)
    assert "FormHeader" not in str(projected)
    assert "technical-id" not in str(projected)


def test_viewer_cannot_open_metadata_readiness_but_operator_gets_typed_contract():
    service = AsyncMock(
        return_value={
            "status": "partial",
            "summary": {"entities": 1, "blocked_entities": 0},
            "entities": [{"id": "performance", "status": "available"}],
            "live_preflight": {"status": "partial"},
        }
    )
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(
        control_room_service,
        "sap_successfactors_talent_metadata_readiness",
        service,
    ):
        viewer = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/talent/metadata-readiness"
        )
        operator = client(OPERATOR).get(
            "/api/control-room/sap-successfactors/talent/metadata-readiness"
        )

    assert viewer.status_code == 403
    assert operator.status_code == 200
    assert operator.json()["components"][0]["component"] == "Desempeno"
    assert "entities" not in operator.json()
    service.assert_awaited_once()


def test_viewer_talent_models_drop_operational_diagnostics():
    payload = {
        "readiness": {
            "profiled_employees": 0,
            "operational_status": "private",
            "operational_label": "private",
            "readiness_status": "private",
            "source_mode": "private",
            "latest_analysis_status": "private",
        },
        "metadata_readiness": {
            "entities": [{"id": "performance"}],
            "live_preflight": {"status": "ready"},
        },
    }
    projected = responses.ControlRoomTalentOverviewResponse.project(
        payload
    ).model_dump()

    assert projected["readiness"]["profiled_employees"] == 0
    assert not {
        "operational_status",
        "operational_label",
        "readiness_status",
        "source_mode",
        "latest_analysis_status",
    } & set(projected["readiness"])
    assert "metadata_readiness" not in projected


@pytest.mark.asyncio
async def test_talent_overview_does_not_invoke_operational_metadata_probe():
    metadata = AsyncMock(side_effect=AssertionError("metadata probe reached"))
    with (
        patch.object(
            control_room_service,
            "sap_successfactors_talent_kpis",
            AsyncMock(return_value={}),
        ),
        patch.object(
            control_room_service,
            "sap_successfactors_talent_9box",
            AsyncMock(return_value={}),
        ),
        patch.object(
            control_room_service,
            "sap_successfactors_talent_anomalies",
            AsyncMock(return_value={}),
        ),
        patch.object(
            control_room_service,
            "sap_successfactors_talent_metadata_readiness",
            metadata,
        ),
    ):
        result = await control_room_service.sap_successfactors_talent_overview({})

    assert "metadata_readiness" not in result
    metadata.assert_not_awaited()

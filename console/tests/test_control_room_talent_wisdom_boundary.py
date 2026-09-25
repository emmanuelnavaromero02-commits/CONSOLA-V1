from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.routers import intelligence
from app.services import control_room_service


SECRET = "wisdom-operational-secret"
USER = {
    "id": 7,
    "role": "workspace_admin",
    "active_tenant_id": "11111111-1111-1111-1111-111111111111",
    "active_workspace_id": "22222222-2222-2222-2222-222222222222",
}


@pytest.mark.asyncio
async def test_wisdom_bit_uses_public_talent_projections_without_metadata_probe(
    monkeypatch,
) -> None:
    overview = AsyncMock(
        return_value={
            "generated_at": "2026-07-26T10:00:00Z",
            "profile": {"industry": "retail", "tenant_id": SECRET},
            "readiness": {
                "status": "partial",
                "profiled_employees": 3,
                "operational_status": SECRET,
                "readiness_status": SECRET,
                "source_mode": SECRET,
                "latest_analysis_status": SECRET,
            },
            "blockers": [
                {
                    "id": "performance",
                    "status": "blocked",
                    "title": "Completar datos de talento",
                    "detail": SECRET,
                }
            ],
            "metadata_readiness": {
                "entities": [{"id": "technical-entity"}],
                "live_preflight": {"status": SECRET},
            },
        }
    )
    anomalies = AsyncMock(
        return_value={
            "status": "partial",
            "summary": {"total": 1, "high": 1},
            "items": [
                {
                    "severity": "high",
                    "title": "Revisar cobertura",
                    "affected_count": 1,
                    "metadata": {"password": SECRET},
                }
            ],
        }
    )
    metadata = AsyncMock(side_effect=AssertionError("metadata probe reached"))
    monkeypatch.setattr(intelligence, "_internal_mcp_user", lambda *_args: USER)
    monkeypatch.setattr(
        control_room_service, "sap_successfactors_talent_overview", overview
    )
    monkeypatch.setattr(
        control_room_service, "sap_successfactors_talent_anomalies", anomalies
    )
    monkeypatch.setattr(
        control_room_service,
        "sap_successfactors_talent_metadata_readiness",
        metadata,
    )

    result = await intelligence.intelligence_wisdom_bits_run_internal(
        intelligence.InternalMcpWisdomBitRequest(
            security_context={},
            wisdom_bit_id="WB-TALENTO",
            cartridge_id="sap_successfactors",
        ),
        internal_service="mcp-infra",
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["coverage"]["profiled_employees"] == 3
    assert result["signals"]["count"] == 1
    assert result["blockers"][0]["title"] == "Completar datos de talento"
    for private in (
        SECRET,
        "operational_status",
        "readiness_status",
        "source_mode",
        "latest_analysis_status",
        "metadata_readiness",
        "live_preflight",
        "tenant_id",
        "metadata",
    ):
        assert private not in serialized
    metadata.assert_not_awaited()

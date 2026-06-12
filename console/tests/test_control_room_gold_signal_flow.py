from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import pytest

from app.services import control_room_service, intelligence_engine


TENANT_A = "11111111-1111-1111-1111-111111111111"
WORKSPACE_A = "22222222-2222-2222-2222-222222222222"
TENANT_B = "33333333-3333-3333-3333-333333333333"
WORKSPACE_B = "44444444-4444-4444-4444-444444444444"

REPLICON_USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": TENANT_A,
    "active_workspace_id": WORKSPACE_A,
    "allowed_cartridges": ["replicon"],
    "role": "admin",
}


async def replicon_gold_fetcher(dataset: str, user: dict | None, limit: int) -> list[dict]:
    assert user == REPLICON_USER
    assert limit >= 3
    if dataset != "consultor_mensual":
        return []
    return [
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-04-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 124,
        },
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-05-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 130,
        },
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-06-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 40,
        },
    ]


@pytest.mark.asyncio
async def test_replicon_gold_generates_scoped_intelligence_signal_with_evidence():
    result = await intelligence_engine.run_intelligence(
        REPLICON_USER,
        {"cartridge_id": "replicon", "metrics": ["billable_hours"], "horizon_days": []},
        fetcher=replicon_gold_fetcher,
        persist=False,
    )

    assert result["skipped"] == []
    assert result["signals"]
    artifact = next(item for item in result["artifacts"] if item["signal"]["signal_subtype"] == "observed")
    signal = artifact["signal"]
    evidence = artifact["evidence_pack"]
    item = evidence["items"][0]

    assert signal["cartridge_id"] == "replicon"
    assert signal["source_system"] == "replicon"
    assert signal["dataset"] == "consultor_mensual"
    assert signal["source_dataset"] == "consultor_mensual"
    assert signal["gold_table"] == "gold_consultor_mensual"
    assert signal["freshness_at"] == "2026-06-01"
    assert signal["freshness_field"] == "mes"
    assert signal["severity"] in {"critical", "high"}
    assert item["source_ref"] == "consultor_mensual"
    assert item["query_text"].startswith("SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual")
    assert item["data"]["row_count"] == 3
    assert item["data"]["sample_hash"]
    assert item["data"]["source_system"] == "replicon"
    assert item["data"]["gold_table"] == "gold_consultor_mensual"
    assert item["metadata"]["freshness_at"] == "2026-06-01"
    assert evidence["source_system"] == "replicon"
    assert evidence["gold_table"] == "gold_consultor_mensual"


@pytest.mark.asyncio
async def test_missing_gold_dataset_is_reported_without_inventing_signals():
    async def missing_gold(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        raise HTTPException(404, f"dataset unavailable: {dataset}")

    result = await intelligence_engine.run_intelligence(
        REPLICON_USER,
        {"cartridge_id": "replicon", "metrics": ["billable_hours"]},
        fetcher=missing_gold,
        persist=False,
    )

    assert result["signals"] == []
    assert result["artifacts"] == []
    assert result["skipped"]
    assert all(item["status"] == "dataset_unavailable" for item in result["skipped"])
    assert {
        "cartridge_id": "replicon",
        "dataset": "consultor_mensual",
        "metric": "billable_hours",
        "status": "dataset_unavailable",
        "reason": "dataset unavailable: consultor_mensual",
    } in result["skipped"]


@pytest.mark.asyncio
async def test_control_room_lists_persisted_gold_signal_with_source_evidence_and_scope():
    metadata = {
        "tenant_id": TENANT_A,
        "workspace_id": WORKSPACE_A,
        "source_system": "replicon",
        "source_dataset": "consultor_mensual",
        "dataset": "consultor_mensual",
        "gold_table": "gold_consultor_mensual",
        "freshness_at": "2026-06-01",
        "freshness_field": "mes",
        "data_status": "gold_ready",
        "evidence_pack_id": 42,
        "evidence_pack": {
            "id": 42,
            "summary": "Baseline moving_average con 2 muestras historicas.",
            "confidence": 0.85,
            "items": [
                {
                    "source_type": "dataset",
                    "source_ref": "consultor_mensual",
                    "data": {"row_count": 3, "sample_hash": "abc123"},
                }
            ],
        },
        "module": "Intelligence Engine",
        "description": "Horas facturables mensuales por consultor: Andrea Morales bajo baseline.",
        "recommendation": "Pedir seguimiento al manager",
        "root_cause": "Cambio de capacidad o asignacion",
        "impact": "Desviacion -87.00 en Horas facturables mensuales por consultor.",
        "details": {
            "actual_value": 40,
            "expected_value": 127,
            "deviation_pct": -0.685,
            "evidence_pack_id": 42,
            "source_system": "replicon",
            "source_dataset": "consultor_mensual",
            "gold_table": "gold_consultor_mensual",
            "freshness_at": "2026-06-01",
            "freshness_field": "mes",
        },
        "sql": "SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual WHERE consultor = $entity_id ORDER BY mes",
        "intelligence": {"signal": {"signal_id": "intel:replicon-gold"}},
    }
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(
        return_value=[
            {
                "tenant_id": TENANT_A,
                "workspace_id": WORKSPACE_A,
                "item_id": "intel:replicon-gold",
                "cartridge_id": "replicon",
                "domain": "Rentabilidad",
                "source_dataset": "consultor_mensual",
                "item_kind": "intelligence_signal",
                "title": "Horas facturables mensuales por consultor",
                "severity": "critical",
                "status": "open",
                "decision_id": None,
                "entity_kind": "consultant",
                "entity_id": "Andrea Morales",
                "entity_label": "Andrea Morales",
                "anomaly_type": "billable_hours",
                "metadata": metadata,
                "first_seen_at": datetime(2026, 6, 12, tzinfo=UTC),
                "last_seen_at": datetime(2026, 6, 12, tzinfo=UTC),
                "resolved_at": None,
                "dismissed_at": None,
                "impact_estimate": 87,
                "impact_currency": "USD",
                "confidence": 0.85,
                "priority_score": 90,
                "selected_option_id": None,
                "execution_status": "not_started",
            }
        ]
    )

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        items = await control_room_service._persisted_intelligence_items(REPLICON_USER)

    sql, workspace_arg, tenant_arg = mock_pool.fetch.await_args.args
    assert "tenant_id::text" in sql
    assert workspace_arg == WORKSPACE_A
    assert tenant_arg == TENANT_A
    assert len(items) == 1
    item = items[0]
    assert item["tenant_id"] == TENANT_A
    assert item["workspace_id"] == WORKSPACE_A
    assert item["source_system"] == "replicon"
    assert item["dataset"] == "consultor_mensual"
    assert item["gold_table"] == "gold_consultor_mensual"
    assert item["freshness_at"] == "2026-06-01"
    assert item["evidence_pack_id"] == 42
    assert item["evidence_pack"]["items"][0]["source_ref"] == "consultor_mensual"
    assert item["details"]["source_dataset"] == "consultor_mensual"
    assert item["sql"].startswith("SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual")


@pytest.mark.asyncio
async def test_control_room_persisted_signal_read_is_scoped_by_tenant_and_workspace():
    async def scoped_fetch(query: str, workspace_id: str, tenant_id: str):
        assert "workspace_id = $1" in query
        assert "tenant_id::text = $2" in query
        if workspace_id == WORKSPACE_A and tenant_id == TENANT_A:
            return [{"item_id": "intel:a", "metadata": {}, "item_kind": "intelligence_signal"}]
        return []

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=scoped_fetch)
    user_b = {
        **REPLICON_USER,
        "active_tenant_id": TENANT_B,
        "active_workspace_id": WORKSPACE_B,
    }

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        a_items = await control_room_service._persisted_intelligence_items(REPLICON_USER)
        b_items = await control_room_service._persisted_intelligence_items(user_b)

    assert [item["id"] for item in a_items] == ["intel:a"]
    assert b_items == []
    assert mock_pool.fetch.await_args_list[0].args[1:] == (WORKSPACE_A, TENANT_A)
    assert mock_pool.fetch.await_args_list[1].args[1:] == (WORKSPACE_B, TENANT_B)

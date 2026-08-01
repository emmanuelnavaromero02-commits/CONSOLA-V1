from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import intelligence as intelligence_router


def _request() -> intelligence_router.GoldRefreshIntelligenceRequest:
    return intelligence_router.GoldRefreshIntelligenceRequest(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        cartridge_id="hubspot",
        airflow_dag_run_id="scheduled__2026-06-19T00:00:00+00:00",
        pipeline_run_id="dataset_refresh_chain:scheduled__2026-06-19T00:00:00+00:00",
        materialization_status="success",
        datasets=["forecast_mensual", "unused_dataset"],
        finished_at="2026-06-19T00:05:00+00:00",
    )


@pytest.mark.asyncio
async def test_gold_refresh_internal_requires_airflow_service():
    with pytest.raises(HTTPException) as exc:
        await intelligence_router.intelligence_gold_refresh_internal(
            _request(),
            internal_service="workspace",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "only airflow can trigger Gold refresh intelligence"


@pytest.mark.asyncio
async def test_gold_refresh_internal_builds_scoped_idempotent_payload(monkeypatch):
    calls: list[dict] = []
    invalidated: list[dict] = []

    async def fake_resolve_scope(
        body: intelligence_router.GoldRefreshIntelligenceRequest,
    ) -> dict:
        return {
            "tenant_id": body.tenant_id,
            "workspace_id": body.workspace_id,
            "tenant_mismatch": False,
            "requested_tenant_id": body.tenant_id,
        }

    async def fake_run_intelligence(user: dict, payload: dict, *, persist: bool):
        calls.append({"user": user, "payload": payload, "persist": persist})
        idempotent = len(calls) > 1
        return {
            "run_ref": payload["run_ref"],
            "intelligence_run_id": 77,
            "signals": [] if idempotent else [{"signal_id": "intel:test"}],
            "skipped": []
            if idempotent
            else [{"status": "missing_simulation_template"}],
            "skipped_counts": {} if idempotent else {"missing_simulation_template": 1},
            "dataset_unavailable_count": 0,
            "insufficient_history_count": 0,
            "idempotent": idempotent,
            "signals_generated": 1 if idempotent else 0,
            "status": "completed",
        }

    def fake_invalidate(user: dict) -> None:
        invalidated.append(user)

    monkeypatch.setattr(
        intelligence_router.intelligence_engine,
        "run_intelligence",
        fake_run_intelligence,
    )
    monkeypatch.setattr(
        intelligence_router,
        "_resolve_gold_refresh_scope",
        fake_resolve_scope,
    )
    monkeypatch.setattr(
        intelligence_router,
        "_invalidate_control_room_cache",
        fake_invalidate,
    )

    response = await intelligence_router.intelligence_gold_refresh_internal(
        _request(),
        internal_service="airflow",
    )
    retry_response = await intelligence_router.intelligence_gold_refresh_internal(
        _request(),
        internal_service="airflow",
    )

    assert response["ok"] is True
    assert response["signals"] == 1
    assert response["skipped"] == 1
    assert response["idempotent"] is False
    assert retry_response["ok"] is True
    assert retry_response["signals"] == 1
    assert retry_response["skipped"] == 0
    assert retry_response["idempotent"] is True
    assert response["run_ref"] == (
        "gold-refresh:" "workspace-1:" "hubspot:" "scheduled__2026-06-19T00:00:00+00:00"
    )
    assert invalidated[0]["active_workspace_id"] == "workspace-1"
    assert invalidated[1]["active_workspace_id"] == "workspace-1"
    assert len(calls) == 2
    call = calls[0]
    assert call["persist"] is True
    assert call["user"]["email"] == "airflow@internal"
    assert call["user"]["tenant_id"] == "tenant-1"
    assert call["user"]["workspace_id"] == "workspace-1"
    assert call["payload"]["run_mode"] == "gold_refresh"
    assert call["payload"]["include_external"] is False
    assert call["payload"]["datasets"] == ["forecast_mensual", "unused_dataset"]
    assert call["payload"]["metadata"]["trigger"] == "gold_refresh"
    assert call["payload"]["metadata"]["datasets_received"] == [
        "forecast_mensual",
        "unused_dataset",
    ]
    assert call["payload"]["metadata"]["requested_tenant_id"] == "tenant-1"
    assert call["payload"]["metadata"]["canonical_tenant_id"] == "tenant-1"
    assert call["payload"]["metadata"]["tenant_mismatch"] is False


@pytest.mark.asyncio
async def test_gold_refresh_internal_uses_workspace_tenant_when_gold_scope_is_stale(
    monkeypatch,
):
    calls: list[dict] = []

    async def fake_resolve_scope(
        body: intelligence_router.GoldRefreshIntelligenceRequest,
    ) -> dict:
        return {
            "tenant_id": "tenant-canonical",
            "workspace_id": body.workspace_id,
            "tenant_mismatch": True,
            "requested_tenant_id": body.tenant_id,
        }

    async def fake_run_intelligence(user: dict, payload: dict, *, persist: bool):
        calls.append({"user": user, "payload": payload, "persist": persist})
        return {
            "run_ref": payload["run_ref"],
            "intelligence_run_id": 88,
            "signals": [{"signal_id": "intel:test"}],
            "skipped": [],
            "skipped_counts": {},
            "dataset_unavailable_count": 0,
            "insufficient_history_count": 0,
            "idempotent": False,
        }

    monkeypatch.setattr(
        intelligence_router,
        "_resolve_gold_refresh_scope",
        fake_resolve_scope,
    )
    monkeypatch.setattr(
        intelligence_router.intelligence_engine,
        "run_intelligence",
        fake_run_intelligence,
    )
    monkeypatch.setattr(
        intelligence_router,
        "_invalidate_control_room_cache",
        lambda user: None,
    )

    response = await intelligence_router.intelligence_gold_refresh_internal(
        _request(),
        internal_service="airflow",
    )

    assert response["ok"] is True
    assert calls[0]["user"]["tenant_id"] == "tenant-canonical"
    assert calls[0]["user"]["active_tenant_id"] == "tenant-canonical"
    assert calls[0]["user"]["workspace_id"] == "workspace-1"
    assert calls[0]["payload"]["metadata"]["requested_tenant_id"] == "tenant-1"
    assert calls[0]["payload"]["metadata"]["canonical_tenant_id"] == "tenant-canonical"
    assert calls[0]["payload"]["metadata"]["tenant_mismatch"] is True

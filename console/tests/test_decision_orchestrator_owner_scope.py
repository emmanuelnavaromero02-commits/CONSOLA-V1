from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from app.dependencies import require_authenticated
from app.routers import intelligence as routes
from app.services.csrf import require_csrf
from app.services.intelligence import decision_orchestrator as orchestrator


TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"


def _source(source_type: str, *, owner_id: int, technical: bool = False) -> dict:
    item_kind = "source_state" if technical else source_type
    if source_type == "control_room_item" and not technical:
        item_kind = "anomaly"
    return {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "owner_user_id": owner_id,
        "source_id": f"{source_type}-1",
        "item_kind": item_kind,
        "title": "Scoped source",
        "severity": "high",
        "status": "open",
        "domain": "Operacion",
        "source_dataset": "gold_metrics",
        "dataset": "gold_metrics",
        "metadata": {"evidence_refs": ["gold_metrics:source-1"]},
    }


class _OwnerConnection:
    def __init__(self, row: dict):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        self.calls.append((" ".join(query.split()), args))
        owner_id = args[3]
        if owner_id is not None and owner_id != self.row["owner_user_id"]:
            return None
        return self.row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_type",
    ["control_room_item", "agent_alert", "intelligence_signal"],
)
async def test_wrong_owner_is_hidden_for_all_owned_source_types(source_type):
    conn = _OwnerConnection(_source(source_type, owner_id=41))

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator._load_source(
            conn,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            source_type=source_type,
            source_id=f"{source_type}-1",
            payload={},
            owner_id=42,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "orchestrator source not found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_type",
    ["control_room_item", "agent_alert", "intelligence_signal"],
)
async def test_visible_technical_source_is_rejected_with_conflict(source_type):
    conn = _OwnerConnection(_source(source_type, owner_id=42, technical=True))

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator._load_source(
            conn,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            source_type=source_type,
            source_id=f"{source_type}-1",
            payload={},
            owner_id=42,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "item_not_business_eligible"


@pytest.mark.asyncio
async def test_workspace_wide_admin_can_load_other_owner_source():
    conn = _OwnerConnection(_source("control_room_item", owner_id=41))

    result = await orchestrator._load_source(
        conn,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        source_type="control_room_item",
        source_id="control_room_item-1",
        payload={},
        owner_id=None,
    )

    assert result["source_id"] == "control_room_item-1"


@pytest.mark.asyncio
async def test_orchestrator_route_rejects_missing_write_permission_before_service():
    user = {
        "id": 42,
        "role": "viewer",
        "active_tenant_id": TENANT_ID,
        "active_workspace_id": WORKSPACE_ID,
    }
    app = FastAPI()

    @app.middleware("http")
    async def _user_state(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    app.include_router(routes.router)
    app.dependency_overrides[require_authenticated] = lambda: user
    app.dependency_overrides[require_csrf] = lambda: None
    blocked = AsyncMock()
    transport = httpx.ASGITransport(app=app)
    with patch.object(orchestrator, "orchestrate", new=blocked):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/intelligence/orchestrate",
                json={
                    "source_type": "control_room_item",
                    "source_id": "control_room_item-1",
                },
            )

    assert response.status_code == 403
    blocked.assert_not_awaited()

from __future__ import annotations

import inspect
import json

import pytest

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from tests.decision_orchestrator_harness import (
    MIGRATION,
    REPO,
    FakeOrchestratorDB,
    _patch_pool,
    _user,
    orchestrator,
)


def test_decision_orchestrator_migration_and_router_contracts():
    sql = MIGRATION.read_text(encoding="utf-8")
    router = (REPO / "console" / "app" / "routers" / "intelligence.py").read_text(
        encoding="utf-8"
    )
    mcp_tools = (REPO / "mcp-infra" / "app" / "tools" / "control_room.py").read_text(
        encoding="utf-8"
    )
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "decision_orchestration_runs" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "'wisdom_bit'" in sql
    assert "99zb_decision_orchestrator_wisdom_bit_source.sql" in (
        REPO / "infra" / "init" / "99zb_decision_orchestrator_wisdom_bit_source.sql"
    ).read_text(encoding="utf-8")
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert "BYPASSRLS" not in sql

    assert '"/orchestrate"' in router
    assert '"wisdom_bit"' in router
    assert '"wisdom_bit"' in mcp_tools
    assert '"/api/v1/intelligence"' in router
    assert "require_csrf" in router
    assert 'require_permission("control_room.write")' in router
    assert 'require_permission("datasets.read")' in router
    assert "scope variables are not accepted" in router

    assert "decision-orchestrator-aws-probe:" in makefile
    assert "scripts/aws_decision_orchestrator_probe.py" in makefile


def test_canonical_signal_metadata_overrides_historical_item_metadata(orchestrator):
    source = inspect.getsource(orchestrator._load_source)
    item_metadata = "COALESCE(item.metadata, '{}'::jsonb)"
    signal_metadata = "COALESCE(signal.metadata, '{}'::jsonb)"

    assert source.index(item_metadata) < source.index(signal_metadata)


@pytest.mark.asyncio
async def test_orchestrator_rejects_diagnostic_control_room_source(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(12)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="source-state-1",
        item_kind="source_state",
        title="Source unavailable",
        metadata={"data_status": "missing"},
    )

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator.orchestrate(
            user,
            {"source_type": "control_room_item", "source_id": "source-state-1"},
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "item_not_business_eligible"
    assert db.runs == {}


@pytest.mark.asyncio
async def test_orchestrator_rejects_technical_agent_alert_with_json_metadata(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(13)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="technical-alert",
        item_kind="agent_alert",
        metadata=json.dumps({"data_status": "missing"}),
    )

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator.orchestrate(
            user,
            {"source_type": "agent_alert", "source_id": "technical-alert"},
        )

    assert exc.value.status_code == 409
    assert db.runs == {}


@pytest.mark.asyncio
async def test_orchestrator_rejects_ineligible_intelligence_signal(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(131)
    db.sources[
        (
            user["active_workspace_id"],
            "intelligence_signal",
            "technical-signal",
        )
    ] = {
        "tenant_id": user["active_tenant_id"],
        "workspace_id": user["active_workspace_id"],
        "source_id": "technical-signal",
        "dataset": "gold_metrics",
        "title": "Technical signal",
        "summary": "Source state only",
        "metadata": {"item_kind": "source_state", "data_status": "missing"},
    }

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator.orchestrate(
            user,
            {"source_type": "intelligence_signal", "source_id": "technical-signal"},
        )

    assert exc.value.status_code == 409
    assert db.runs == {}


@pytest.mark.asyncio
async def test_orchestrator_accepts_canonical_intelligence_signal_metadata(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(132)
    db.sources[(user["active_workspace_id"], "intelligence_signal", "signal-1")] = {
        "tenant_id": user["active_tenant_id"],
        "workspace_id": user["active_workspace_id"],
        "source_id": "signal-1",
        "item_kind": "intelligence_signal",
        "dataset": "gold_metrics",
        "source_dataset": "gold_metrics",
        "actual_value": 7,
        "summary": "Observed workforce deviation",
        "metadata": {
            "data_status": "gold_ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 7,
            "evidence_pack": {"items": ["gold_metrics:record:signal-1"]},
        },
    }

    result = await orchestrator.orchestrate(
        user,
        {"source_type": "intelligence_signal", "source_id": "signal-1"},
    )

    assert result["orchestration"]["source_id"] == "signal-1"
    assert db.runs


@pytest.mark.asyncio
async def test_orchestrator_requires_an_eligible_parent(orchestrator, monkeypatch):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(14)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="technical-parent",
        item_kind="source_state",
        metadata={"data_status": "missing"},
    )
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="derived-alert",
        item_kind="agent_alert",
        metadata={"parent_item_id": "technical-parent", "data_status": "ready"},
    )

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator.orchestrate(
            user,
            {"source_type": "agent_alert", "source_id": "derived-alert"},
        )

    assert exc.value.status_code == 409
    assert db.runs == {}


@pytest.mark.asyncio
async def test_orchestrator_rejects_a_missing_parent(orchestrator, monkeypatch):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(15)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="orphan-alert",
        item_kind="agent_alert",
        metadata={"parent_item_id": "missing-parent", "data_status": "ready"},
    )

    with pytest.raises(orchestrator.DecisionOrchestratorError) as exc:
        await orchestrator.orchestrate(
            user,
            {"source_type": "agent_alert", "source_id": "orphan-alert"},
        )

    assert exc.value.status_code == 409
    assert db.runs == {}


@pytest.mark.asyncio
async def test_orchestrator_accepts_an_eligible_parent(orchestrator, monkeypatch):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(16)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="business-parent",
        item_kind="anomaly",
        source_dataset="gold_metrics",
        metadata={
            "data_status": "ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 1,
            **runtime_row_evidence_fields(
                source_dataset="gold_metrics",
                source_row={"item_id": "business-parent"},
                locator_field="item_id",
                observed_at="2026-07-10T00:00:00Z",
            ),
        },
    )
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="business-alert",
        item_kind="agent_alert",
        source_dataset="gold_metrics",
        metadata={
            "parent_item_id": "business-parent",
            "data_status": "ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 1,
            **runtime_row_evidence_fields(
                source_dataset="gold_metrics",
                source_row={"item_id": "business-alert"},
                locator_field="item_id",
                observed_at="2026-07-10T00:00:00Z",
            ),
        },
    )

    result = await orchestrator.orchestrate(
        user,
        {"source_type": "agent_alert", "source_id": "business-alert"},
    )

    assert result["orchestration"]["source_id"] == "business-alert"

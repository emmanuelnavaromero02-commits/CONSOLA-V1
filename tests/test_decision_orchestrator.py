from __future__ import annotations

import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99u_decision_orchestrator.sql"


@pytest.fixture()
def orchestrator(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services.intelligence import decision_orchestrator as mod

    monkeypatch.delenv("DECISION_ORCHESTRATOR_CREATE_ACTIONS", raising=False)
    monkeypatch.delenv("DECISION_ORCHESTRATOR_ALLOW_MANUAL_FIXTURE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    return mod


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, db: "FakeOrchestratorDB"):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, db: "FakeOrchestratorDB"):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)


class FakeOrchestratorDB:
    def __init__(self):
        self.scope_calls: list[tuple[str | None, str]] = []
        self.current_tenant_id: str | None = None
        self.current_workspace_id: str | None = None
        self.sources: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.runs: dict[tuple[str, str], dict[str, Any]] = {}

    def transaction(self):
        return _Tx()

    def add_control_room_item(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        item_id: str,
        item_kind: str = "intelligence_signal",
        title: str = "Forecast risk",
        metadata: Any = None,
    ) -> None:
        self.sources[(workspace_id, "control_room_item", item_id)] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "source_id": item_id,
            "item_kind": item_kind,
            "title": title,
            "severity": "high",
            "status": "open",
            "domain": "Operacion",
            "source_dataset": "intelligence_signals",
            "metadata": metadata or {},
        }
        if item_kind == "agent_alert":
            self.sources[(workspace_id, "agent_alert", item_id)] = {
                **self.sources[(workspace_id, "control_room_item", item_id)],
                "item_kind": "agent_alert",
            }

    def _visible(self, row: dict[str, Any] | None) -> bool:
        if not row or not self.current_workspace_id:
            return False
        return str(row["workspace_id"]) == str(self.current_workspace_id) and str(
            row.get("tenant_id") or ""
        ) == str(self.current_tenant_id or "")

    async def execute(self, query: str, *args):
        if "set_config('app.tenant_id'" in query:
            self.current_tenant_id = str(args[0]) if args[0] else None
            self.current_workspace_id = str(args[1])
            self.scope_calls.append((self.current_tenant_id, self.current_workspace_id))
            return None
        raise AssertionError(f"unmocked execute: {' '.join(query.split())[:180]}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "FROM control_room_items" in q:
            workspace_id, source_id = str(args[0]), str(args[1])
            source_type = (
                "agent_alert"
                if "item_kind = 'agent_alert'" in q
                else "control_room_item"
            )
            row = self.sources.get((workspace_id, source_type, source_id))
            return row if self._visible(row) else None
        if "FROM intelligence_signals" in q:
            row = self.sources.get((str(args[0]), "intelligence_signal", str(args[1])))
            return row if self._visible(row) else None
        if "FROM monte_carlo_simulations" in q:
            row = self.sources.get(
                (str(args[0]), "monte_carlo_simulation", str(args[1]))
            )
            return row if self._visible(row) else None
        if "FROM calibration_observations" in q:
            row = self.sources.get(
                (str(args[0]), "calibration_observation", str(args[1]))
            )
            return row if self._visible(row) else None
        if q.startswith("INSERT INTO decision_orchestration_runs"):
            (
                orchestration_id,
                tenant_id,
                workspace_id,
                source_type,
                source_id,
                problem_type,
                secondary_problem_types,
                confidence,
                recommended_engines,
                candidate_engines,
                engine_plan,
                decision_plan,
                action_recommended,
                reasoning_summary,
                safety_notes,
                missing_data,
                created_by,
            ) = args
            key = (str(workspace_id), str(orchestration_id))
            existing = self.runs.get(key, {})
            row = {
                **existing,
                "id": existing.get("id") or len(self.runs) + 1,
                "orchestration_id": orchestration_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "source_id": source_id,
                "problem_type": problem_type,
                "secondary_problem_types": secondary_problem_types,
                "confidence": confidence,
                "recommended_engines": recommended_engines,
                "candidate_engines": candidate_engines,
                "engine_plan": engine_plan,
                "decision_plan": decision_plan,
                "action_recommended": action_recommended,
                "external_action_id": existing.get("external_action_id"),
                "reasoning_summary": reasoning_summary,
                "safety_notes": safety_notes,
                "missing_data": missing_data,
                "created_by": created_by,
                "created_at": existing.get("created_at") or datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            self.runs[key] = row
            return row
        if q.startswith("UPDATE decision_orchestration_runs SET external_action_id"):
            workspace_id, orchestration_id, action_id = (
                str(args[0]),
                str(args[1]),
                str(args[2]),
            )
            row = self.runs.get((workspace_id, orchestration_id))
            if not self._visible(row):
                return None
            row["external_action_id"] = action_id
            row["updated_at"] = datetime.now(UTC)
            return row
        if q.startswith("SELECT * FROM decision_orchestration_runs"):
            row = self.runs.get((str(args[0]), str(args[1])))
            return row if self._visible(row) else None
        raise AssertionError(f"unmocked fetchrow: {q[:180]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("WITH RECURSIVE lineage AS"):
            workspace_id = str(args[0])
            pending = list(args[1])
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            while pending:
                item_id = str(pending.pop())
                if item_id in seen:
                    continue
                seen.add(item_id)
                row = self.sources.get((workspace_id, "control_room_item", item_id))
                if not self._visible(row):
                    continue
                rows.append(row)
                metadata = row.get("metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                lineage = metadata.get("lineage") or {}
                for parent_id in (
                    metadata.get("parent_item_id"),
                    metadata.get("source_item_id"),
                    metadata.get("derived_from"),
                    lineage.get("parent_item_id"),
                    lineage.get("source_item_id"),
                ):
                    if isinstance(parent_id, str) and parent_id:
                        pending.append(parent_id)
            return rows
        if q.startswith("SELECT * FROM decision_orchestration_runs"):
            workspace_id = str(args[0])
            rows = [
                row
                for (row_workspace, _run_id), row in self.runs.items()
                if row_workspace == workspace_id and self._visible(row)
            ]
            if "source_type =" in q:
                rows = [row for row in rows if row["source_type"] == args[1]]
            if "source_id =" in q:
                source_id = args[2] if "source_type =" in q else args[1]
                rows = [row for row in rows if row["source_id"] == source_id]
            return rows[: int(args[-1])]
        raise AssertionError(f"unmocked fetch: {q[:180]}")


def _user(
    user_id: int,
    *,
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    workspace_id: str = "22222222-2222-2222-2222-222222222222",
) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"user{user_id}@example.com",
        "role": "workspace_admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


def _patch_pool(mod, monkeypatch, db: FakeOrchestratorDB) -> None:
    monkeypatch.setattr(mod.auth, "pool", AsyncMock(return_value=FakePool(db)))


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


def test_classifier_routes_available_and_candidate_engines(orchestrator):
    resource = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "resource-1",
            "title": "Allocate staffing resources for forecast demand",
            "description": "Need allocation under constraints and capacity risk.",
            "constraints": {"hours": 120},
            "evidence_refs": [{"type": "control_room_item", "id": "x"}],
        },
        {"source_id": "resource-1"},
    )
    assert resource["problem_type"] == "resource_allocation"
    assert [item["name"] for item in resource["recommended_engines"]] == [
        "decision_intelligence",
        "monte_carlo",
    ]
    assert resource["candidate_engines"][0]["name"] == "constrained_optimizer_candidate"
    assert resource["engine_plan"]["candidate_engines_executed"] is False

    temporal = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "temporal-1",
            "title": "Real-time feedback control for delay risk",
            "description": "Dynamic trajectory with lead time and probability breach.",
            "time_horizon": "next 30 days",
            "evidence_refs": [{"type": "signal", "id": "s"}],
        },
        {"source_id": "temporal-1"},
    )
    assert temporal["problem_type"] == "temporal_control"
    assert temporal["candidate_engines"][0]["name"] == "mpc_candidate"

    strategy = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "strategy-1",
            "title": "Vendor negotiation against competitor response",
            "description": "Multi actor strategic market move.",
        },
        {"source_id": "strategy-1"},
    )
    assert strategy["problem_type"] == "multi_actor_strategy"
    assert strategy["candidate_engines"][0]["name"] == "game_theory_candidate"

    simple = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "action-1",
            "title": "Notify owner and create task",
            "description": "Escalate simple remediation.",
        },
        {"source_id": "action-1"},
    )
    assert simple["problem_type"] == "simple_action"
    assert simple["action_recommended"] is True
    assert "external_action_framework" in [
        item["name"] for item in simple["recommended_engines"]
    ]

    insufficient = orchestrator.build_orchestration_plan(
        {"source_type": "manual_fixture", "source_id": "empty"},
        {"source_id": "empty"},
    )
    assert insufficient["problem_type"] == "insufficient_data"
    assert insufficient["action_recommended"] is False


def test_orchestrator_preserves_market_context_evidence_without_execution(orchestrator):
    plan = orchestrator.build_orchestration_plan(
        {
            "source_type": "monte_carlo_simulation",
            "source_id": "mc-1",
            "title": "Forecast risk with external market context",
            "description": "Monte Carlo uncertainty includes governed Banxico context.",
            "evidence_refs": [{"type": "control_room_item", "id": "cri-1"}],
        },
        {
            "source_id": "mc-1",
            "metadata": {
                "evidence_refs": [
                    {
                        "type": "market_context",
                        "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
                    }
                ]
            },
        },
    )

    refs = plan["evidence_refs"]
    assert {"type": "control_room_item", "id": "cri-1"} in refs
    assert {
        "type": "market_context",
        "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
    } in refs
    assert (
        plan["decision_plan"]["external_evidence"]["market_context_policy"]
        == "evidence_only"
    )
    assert plan["engine_plan"]["external_evidence"]["market_context_ref_count"] == 1
    assert plan["engine_plan"]["available_engines_executed"] is False
    assert plan["action_recommended"] is False
    assert any("evidence only" in item for item in plan["safety_notes"])


@pytest.mark.asyncio
async def test_orchestrator_persists_with_scoped_runtime_and_tenant_isolation(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user_a = _user(10)
    user_b = _user(
        11,
        tenant_id="33333333-3333-3333-3333-333333333333",
        workspace_id="44444444-4444-4444-4444-444444444444",
    )
    db.add_control_room_item(
        tenant_id=user_a["active_tenant_id"],
        workspace_id=user_a["active_workspace_id"],
        item_id="item-a",
        title="Forecast anomaly probability breach",
        metadata={"evidence_refs": [{"type": "control_room_item", "id": "item-a"}]},
    )

    result = await orchestrator.orchestrate(
        user_a,
        {"source_type": "control_room_item", "source_id": "item-a"},
    )
    run = result["orchestration"]
    assert run["problem_type"] == "risk_forecast"
    assert run["external_action_id"] is None
    assert db.scope_calls[-1] == (
        user_a["active_tenant_id"],
        user_a["active_workspace_id"],
    )

    listed = await orchestrator.list_orchestrations(
        user_a, source_type="control_room_item"
    )
    assert listed["orchestrations"][0]["orchestration_id"] == run["orchestration_id"]
    detail = await orchestrator.get_orchestration(user_a, run["orchestration_id"])
    assert detail["orchestration"]["orchestration_id"] == run["orchestration_id"]

    assert await orchestrator.list_orchestrations(user_b) == {"orchestrations": []}
    with pytest.raises(orchestrator.DecisionOrchestratorError) as hidden:
        await orchestrator.get_orchestration(user_b, run["orchestration_id"])
    assert hidden.value.status_code == 404

    with pytest.raises(orchestrator.DecisionOrchestratorError) as missing:
        await orchestrator.orchestrate(
            user_a,
            {"source_type": "control_room_item", "source_id": "missing"},
        )
    assert missing.value.status_code == 404


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
            "evidence_pack": {"id": 17, "items": [{"id": "evidence-1"}]},
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
        metadata={"data_status": "ready"},
    )
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="business-alert",
        item_kind="agent_alert",
        metadata={"parent_item_id": "business-parent", "data_status": "ready"},
    )

    result = await orchestrator.orchestrate(
        user,
        {"source_type": "agent_alert", "source_id": "business-alert"},
    )

    assert result["orchestration"]["source_id"] == "business-alert"


@pytest.mark.asyncio
async def test_orchestrator_accepts_wisdombit_monitor_source(orchestrator, monkeypatch):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(12)

    result = await orchestrator.orchestrate(
        user,
        {
            "source_type": "wisdom_bit",
            "source_id": "WB-TALENTO",
            "title": "Decision operativa WB-TALENTO",
            "description": "Aggregated talent readiness monitor with blockers and simulation evidence.",
            "metrics": {"risk_metric": "talent_readiness_delta"},
            "constraints": {"recommendation_only": True},
            "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
        },
    )

    run = result["orchestration"]
    assert run["source_type"] == "wisdom_bit"
    assert run["source_id"] == "WB-TALENTO"
    assert run["problem_type"] in {"risk_forecast", "data_quality"}


@pytest.mark.asyncio
async def test_orchestrator_rejects_payload_scope_and_manual_fixture_without_flag(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(20)

    with pytest.raises(orchestrator.DecisionOrchestratorError) as scope_error:
        await orchestrator.orchestrate(
            user,
            {
                "source_type": "control_room_item",
                "source_id": "item-a",
                "metrics": {"tenant_id": "malicious"},
            },
        )
    assert scope_error.value.status_code == 422

    with pytest.raises(orchestrator.DecisionOrchestratorError) as fixture_error:
        await orchestrator.orchestrate(
            user,
            {"source_type": "manual_fixture", "source_id": "fixture-1"},
        )
    assert fixture_error.value.status_code == 403


@pytest.mark.asyncio
async def test_orchestrator_optional_external_action_stays_pending_approval(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(30)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="action-source",
        title="Notify owner and create task",
        metadata={
            "evidence_refs": [{"type": "control_room_item", "id": "action-source"}]
        },
    )
    propose_calls: list[dict[str, Any]] = []

    async def fake_propose(_user: dict, payload: dict[str, Any]) -> dict[str, Any]:
        propose_calls.append(payload)
        return {
            "action": {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "status": "pending_approval",
                "adapter_name": "sandbox",
                "metadata": payload["metadata"],
                "execution_result": {},
            }
        }

    monkeypatch.setattr(orchestrator.external_actions, "propose", fake_propose)

    flag_off = await orchestrator.orchestrate(
        user,
        {"source_type": "control_room_item", "source_id": "action-source"},
    )
    assert flag_off["orchestration"]["action_recommended"] is True
    assert flag_off["orchestration"]["external_action_id"] is None
    assert propose_calls == []

    monkeypatch.setenv("DECISION_ORCHESTRATOR_CREATE_ACTIONS", "true")
    flag_on = await orchestrator.orchestrate(
        user,
        {"source_type": "control_room_item", "source_id": "action-source"},
    )
    run = flag_on["orchestration"]
    action = run["external_action"]
    assert run["external_action_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert action["status"] == "pending_approval"
    assert action["metadata"]["created_by_orchestrator"] is True
    assert action["metadata"]["orchestration_id"] == run["orchestration_id"]
    assert action["metadata"]["requires_human_approval"] is True
    assert action["execution_result"] == {}
    assert propose_calls[-1]["adapter_name"] == "sandbox"
    assert propose_calls[-1]["payload"]["orchestration_id"] == run["orchestration_id"]
    assert "tenant_id" not in json.dumps(propose_calls[-1]["payload"])

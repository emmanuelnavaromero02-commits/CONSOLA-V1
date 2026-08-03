from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99v_decision_orchestrator_executions.sql"


@pytest.fixture()
def execution_mod(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services.intelligence import orchestrator_execution as mod

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        mod.truth, "execution_source_trusted", AsyncMock(return_value=True)
    )
    return mod


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, db: "FakeExecutionDB"):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, db: "FakeExecutionDB"):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)


class FakeExecutionDB:
    def __init__(self):
        self.current_tenant_id: str | None = None
        self.current_workspace_id: str | None = None
        self.scope_calls: list[tuple[str | None, str]] = []
        self.runs: dict[tuple[str, str], dict[str, Any]] = {}
        self.executions: list[dict[str, Any]] = []
        self.calibration_states: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.calibration_observations: dict[tuple[str, str], dict[str, Any]] = {}

    def transaction(self):
        return _Tx()

    def add_run(
        self,
        *,
        tenant_id: str = "11111111-1111-1111-1111-111111111111",
        workspace_id: str = "22222222-2222-2222-2222-222222222222",
        orchestration_id: str = "orch-risk",
        problem_type: str = "risk_forecast",
        candidate_engines: list[dict[str, Any]] | None = None,
        external_action_id: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": len(self.runs) + 1,
            "orchestration_id": orchestration_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "source_type": "control_room_item",
            "source_id": "source-1",
            "problem_type": problem_type,
            "secondary_problem_types": [],
            "confidence": 0.82,
            "recommended_engines": [
                {"name": "decision_intelligence"},
                {"name": "monte_carlo"},
                {"name": "bayesian_calibration"},
            ],
            "candidate_engines": candidate_engines or [],
            "engine_plan": {"mode": "plan_only", "available_engines_executed": False},
            "decision_plan": {"next_human_decision": "review_or_collect_more_evidence"},
            "action_recommended": bool(external_action_id),
            "external_action_id": external_action_id,
            "reasoning_summary": "classified",
            "safety_notes": [],
            "missing_data": [],
            "created_by": 7,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        self.runs[(workspace_id, orchestration_id)] = row
        return row

    def add_calibration_state(
        self,
        *,
        workspace_id: str = "22222222-2222-2222-2222-222222222222",
        group: str = "risk:late",
        model_version: str = "bayesian_calibration.v1",
        sample_count: int = 24,
        confidence_score: float = 0.74,
    ) -> None:
        self.calibration_states[(workspace_id, group, model_version)] = {
            "id": 10,
            "tenant_id": self.current_tenant_id or "11111111-1111-1111-1111-111111111111",
            "workspace_id": workspace_id,
            "calibration_group": group,
            "model_version": model_version,
            "posterior": {"alpha": 19, "beta": 7, "mean": 0.730769},
            "metrics": {"sample_count": sample_count, "confidence_score": confidence_score, "complete": True, "provenance_complete": True, "binary_evaluation_complete": True},
            "sample_count": sample_count,
            "confidence_score": confidence_score,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

    def _visible(self, row: dict[str, Any] | None) -> bool:
        if not row or not self.current_workspace_id:
            return False
        return (
            str(row.get("workspace_id")) == str(self.current_workspace_id)
            and str(row.get("tenant_id") or "") == str(self.current_tenant_id or "")
        )

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if "set_config('app.tenant_id'" in query:
            self.current_tenant_id = str(args[0]) if args[0] else None
            self.current_workspace_id = str(args[1])
            self.scope_calls.append((self.current_tenant_id, self.current_workspace_id))
            return None
        if q.startswith("UPDATE decision_orchestration_runs SET engine_plan"):
            workspace_id, orchestration_id, engine_plan = str(args[0]), str(args[1]), args[2]
            row = self.runs.get((workspace_id, orchestration_id))
            if row and self._visible(row):
                row["engine_plan"] = engine_plan
                row["updated_at"] = datetime.now(UTC)
            return "UPDATE 1"
        raise AssertionError(f"unmocked execute: {q[:180]}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT * FROM decision_orchestration_runs"):
            row = self.runs.get((str(args[0]), str(args[1])))
            return row if self._visible(row) else None
        if q.startswith("SELECT * FROM decision_orchestration_executions"):
            workspace_id, orchestration_id, engine_name = str(args[0]), str(args[1]), str(args[2])
            for row in self.executions:
                if (
                    row["workspace_id"] == workspace_id
                    and row["orchestration_id"] == orchestration_id
                    and row["engine_name"] == engine_name
                    and row["execution_status"] in set(args[3])
                    and self._visible(row)
                ):
                    return row
            return None
        if q.startswith("INSERT INTO decision_orchestration_executions"):
            (
                tenant_id,
                workspace_id,
                orchestration_id,
                engine_name,
                execution_status,
                input_hash,
                output_hash,
                input_summary,
                result_summary,
                evidence_refs,
                error_code,
                error_message,
                started_at,
                finished_at,
                created_by,
            ) = args
            for row in self.executions:
                if (
                    row["workspace_id"] == str(workspace_id)
                    and row["orchestration_id"] == str(orchestration_id)
                    and row["engine_name"] == str(engine_name)
                    and row["input_hash"] == str(input_hash)
                ):
                    return row
            row = {
                "id": len(self.executions) + 1,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "orchestration_id": orchestration_id,
                "engine_name": engine_name,
                "execution_status": execution_status,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "input_summary": input_summary,
                "result_summary": result_summary,
                "evidence_refs": evidence_refs,
                "error_code": error_code,
                "error_message": error_message,
                "started_at": started_at,
                "finished_at": finished_at,
                "created_by": created_by,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            self.executions.append(row)
            return row
        if q.startswith("SELECT calibration_group, model_version FROM calibration_observations"):
            row = self.calibration_observations.get((str(args[0]), str(args[1])))
            return row if self._visible(row) else None
        if q.startswith("SELECT * FROM calibration_states"):
            row = self.calibration_states.get((str(args[0]), str(args[1]), str(args[2])))
            return row if row and str(row["workspace_id"]) == str(self.current_workspace_id) else None
        raise AssertionError(f"unmocked fetchrow: {q[:180]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT * FROM decision_orchestration_executions"):
            workspace_id, orchestration_id = str(args[0]), str(args[1])
            return [
                row
                for row in self.executions
                if row["workspace_id"] == workspace_id
                and row["orchestration_id"] == orchestration_id
                and self._visible(row)
            ]
        raise AssertionError(f"unmocked fetch: {q[:180]}")


def _user(
    user_id: int = 7,
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


def _patch_pool(mod, monkeypatch, db: FakeExecutionDB) -> None:
    monkeypatch.setattr(mod.auth, "pool", AsyncMock(return_value=FakePool(db)))


def test_decision_orchestrator_execution_migration_and_router_contracts():
    sql = MIGRATION.read_text(encoding="utf-8")
    router = (REPO / "console" / "app" / "routers" / "intelligence.py").read_text(
        encoding="utf-8"
    )
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "decision_orchestration_executions" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert "BYPASSRLS" not in sql
    assert "UNIQUE (workspace_id, orchestration_id, engine_name, input_hash)" in sql

    assert '"/orchestrate/{orchestration_id}/execute-engines"' in router
    assert '"/orchestrate/{orchestration_id}/executions"' in router
    assert 'require_permission("control_room.write")' in router
    assert 'require_permission("datasets.read")' in router
    assert "scope variables are not accepted" in router

    assert "decision-orchestrator-execution-aws-probe:" in makefile
    assert "scripts/aws_decision_orchestrator_execution_probe.py" in makefile


@pytest.mark.asyncio
async def test_risk_forecast_executes_monte_carlo_and_bayes_idempotently(
    execution_mod, monkeypatch
):
    db = FakeExecutionDB()
    db.add_run()
    db.add_calibration_state()
    _patch_pool(execution_mod, monkeypatch, db)
    monte_carlo_calls: list[dict[str, Any]] = []

    async def fake_run_simulation(_user, payload):
        monte_carlo_calls.append(payload)
        return {
            "simulation": {
                "simulation_id": "mc-123",
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "distribution_summary": {"p50": 12, "breach_probability": 0.34},
                "reproducibility_hash": "hash-123",
            }
        }

    monkeypatch.setattr(
        execution_mod.monte_carlo_service,
        "run_simulation",
        fake_run_simulation,
    )
    payload = {
        "engine_inputs": {
            "monte_carlo": {
                "source_type": "signal",
                "source_id": "sig-1",
                "input_variables": {
                    "baseline_value": {"type": "fixed", "value": 100},
                    "expected_delta": {"type": "fixed", "value": -10},
                },
                "output_metric": "net_value",
            },
            "bayesian_calibration": {"calibration_group": "risk:late"},
        }
    }

    first = await execution_mod.execute_engines(_user(), "orch-risk", payload)
    second = await execution_mod.execute_engines(_user(), "orch-risk", payload)

    assert first["aggregate"]["executed_engines"] == [
        "monte_carlo",
        "bayesian_calibration",
    ]
    assert first["aggregate"]["candidate_engines"] == []
    assert {item["type"] for item in first["aggregate"]["evidence_refs"]} == {
        "monte_carlo_simulation",
        "calibration_state",
    }
    assert second["aggregate"]["executed_engines"] == first["aggregate"]["executed_engines"]
    assert len(monte_carlo_calls) == 1
    assert len([row for row in db.executions if row["engine_name"] == "monte_carlo"]) == 1
    assert db.scope_calls


@pytest.mark.asyncio
async def test_engine_failure_does_not_stop_other_safe_engines(execution_mod, monkeypatch):
    db = FakeExecutionDB()
    db.add_run()
    db.add_calibration_state()
    _patch_pool(execution_mod, monkeypatch, db)

    async def broken_run_simulation(_user, _payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        execution_mod.monte_carlo_service,
        "run_simulation",
        broken_run_simulation,
    )

    result = await execution_mod.execute_engines(
        _user(),
        "orch-risk",
        {
            "engine_inputs": {
                "monte_carlo": {
                    "source_type": "signal",
                    "source_id": "sig-1",
                    "input_variables": {
                        "baseline_value": {"type": "fixed", "value": 100},
                    },
                },
                "bayesian_calibration": {"calibration_group": "risk:late"},
            }
        },
    )

    assert result["aggregate"]["executed_engines"] == ["bayesian_calibration"]
    assert result["aggregate"]["failed_engines"] == [
        {"engine": "monte_carlo", "status": "failed", "reason": "monte_carlo_error"}
    ]


@pytest.mark.asyncio
async def test_missing_data_skips_engines_with_explicit_reasons(execution_mod, monkeypatch):
    db = FakeExecutionDB()
    db.add_run()
    _patch_pool(execution_mod, monkeypatch, db)

    async def fake_run_simulation(_user, _payload):
        raise AssertionError("monte carlo should not run without inputs")

    monkeypatch.setattr(
        execution_mod.monte_carlo_service,
        "run_simulation",
        fake_run_simulation,
    )

    result = await execution_mod.execute_engines(_user(), "orch-risk", {"engine_inputs": {}})
    skipped = {(item["engine"], item["reason"]) for item in result["aggregate"]["skipped_engines"]}

    assert ("monte_carlo", "missing_monte_carlo_inputs") in skipped
    assert ("bayesian_calibration", "missing_calibration_group") in skipped
    assert result["aggregate"]["executed_engines"] == []


@pytest.mark.asyncio
async def test_candidate_engines_are_recorded_but_never_executed(execution_mod, monkeypatch):
    db = FakeExecutionDB()
    db.add_run(
        orchestration_id="orch-temporal",
        problem_type="temporal_control",
        candidate_engines=[{"name": "mpc_candidate", "status": "not_implemented"}],
    )
    _patch_pool(execution_mod, monkeypatch, db)
    monkeypatch.setattr(
        execution_mod.monte_carlo_service,
        "run_simulation",
        AsyncMock(
            return_value={
                "simulation": {
                    "simulation_id": "mc-temporal",
                    "source_type": "signal",
                    "source_id": "sig-1",
                    "distribution_summary": {},
                    "reproducibility_hash": "hash",
                }
            }
        ),
    )

    result = await execution_mod.execute_engines(
        _user(),
        "orch-temporal",
        {
            "engine_inputs": {
                "monte_carlo": {
                    "source_type": "signal",
                    "source_id": "sig-1",
                    "input_variables": {"baseline_value": {"type": "fixed", "value": 1}},
                }
            }
        },
    )

    assert result["aggregate"]["candidate_engines"] == [
        {"engine": "mpc_candidate", "status": "candidate_only"}
    ]
    assert all(row["engine_name"] != "decision_intelligence" for row in db.executions)


@pytest.mark.asyncio
async def test_tenant_b_cannot_execute_or_list_tenant_a_run(execution_mod, monkeypatch):
    db = FakeExecutionDB()
    db.add_run()
    _patch_pool(execution_mod, monkeypatch, db)
    tenant_b = _user(
        tenant_id="33333333-3333-3333-3333-333333333333",
        workspace_id="44444444-4444-4444-4444-444444444444",
    )

    with pytest.raises(execution_mod.OrchestratorExecutionError) as execute_exc:
        await execution_mod.execute_engines(tenant_b, "orch-risk", {"engine_inputs": {}})
    with pytest.raises(execution_mod.OrchestratorExecutionError) as list_exc:
        await execution_mod.list_executions(tenant_b, "orch-risk")

    assert execute_exc.value.status_code == 404
    assert list_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_scope_injection_is_rejected_before_execution(execution_mod, monkeypatch):
    db = FakeExecutionDB()
    db.add_run()
    _patch_pool(execution_mod, monkeypatch, db)

    with pytest.raises(execution_mod.OrchestratorExecutionError) as exc:
        await execution_mod.execute_engines(
            _user(),
            "orch-risk",
            {"engine_inputs": {"monte_carlo": {"tenant_id": "evil"}}},
        )

    assert exc.value.status_code == 422
    assert not db.executions


@pytest.mark.asyncio
async def test_resource_allocation_runs_only_monte_carlo_and_optimizer_candidate(
    execution_mod, monkeypatch
):
    db = FakeExecutionDB()
    db.add_run(
        orchestration_id="orch-resource",
        problem_type="resource_allocation",
        candidate_engines=[
            {"name": "constrained_optimizer_candidate", "status": "not_implemented"}
        ],
    )
    _patch_pool(execution_mod, monkeypatch, db)
    monkeypatch.setattr(
        execution_mod.monte_carlo_service,
        "run_simulation",
        AsyncMock(
            return_value={
                "simulation": {
                    "simulation_id": "mc-resource",
                    "source_type": "signal",
                    "source_id": "sig-2",
                    "distribution_summary": {},
                    "reproducibility_hash": "hash",
                }
            }
        ),
    )

    result = await execution_mod.execute_engines(
        _user(),
        "orch-resource",
        {
            "engine_inputs": {
                "monte_carlo": {
                    "source_type": "signal",
                    "source_id": "sig-2",
                    "input_variables": {"baseline_value": {"type": "fixed", "value": 10}},
                }
            }
        },
    )

    assert result["aggregate"]["executed_engines"] == ["monte_carlo"]
    assert result["aggregate"]["candidate_engines"] == [
        {"engine": "constrained_optimizer_candidate", "status": "candidate_only"}
    ]
    assert all(row["engine_name"] != "bayesian_calibration" for row in db.executions)

from __future__ import annotations

import pytest

from app.services.intelligence.orchestrator_execution_truth import (
    execution_source_trusted,
)
from tests.test_decision_orchestrator_execution import (
    FakeExecutionDB,
    _patch_pool,
    _user,
    execution_mod,
)


class HistoricalManualExecutionDB(FakeExecutionDB):
    control_room_row = None

    async def fetchrow(self, query: str, *args):
        if "FROM monte_carlo_simulations" in query:
            return {"source_type": "manual_fixture", "source_id": "fixture-old"}
        if "metadata->>'origin' = 'wisdom_bit'" in query:
            return None
        if "FROM control_room_items" in query:
            return self.control_room_row
        return await super().fetchrow(query, *args)


@pytest.mark.asyncio
async def test_historical_manual_ancestor_cannot_create_execution_or_candidate(
    execution_mod, monkeypatch
) -> None:
    db = HistoricalManualExecutionDB()
    run = db.add_run(
        candidate_engines=[{"name": "mpc_candidate", "status": "not_implemented"}]
    )
    run.update(
        source_type="monte_carlo_simulation",
        source_id="historical-simulation",
    )
    original_plan = dict(run["engine_plan"])
    _patch_pool(execution_mod, monkeypatch, db)
    monkeypatch.setattr(
        execution_mod.truth, "execution_source_trusted", execution_source_trusted
    )

    with pytest.raises(execution_mod.OrchestratorExecutionError) as exc:
        await execution_mod.execute_engines(_user(), "orch-risk", {"engine_inputs": {}})

    assert (exc.value.status_code, exc.value.detail) == (
        409,
        "source_provenance_untrusted",
    )
    assert db.executions == []
    assert run["engine_plan"] == original_plan


@pytest.mark.asyncio
async def test_missing_historical_control_room_source_fails_before_writes(
    execution_mod, monkeypatch
) -> None:
    db = HistoricalManualExecutionDB()
    run = db.add_run(candidate_engines=[{"name": "mpc_candidate"}])
    original_plan = dict(run["engine_plan"])
    _patch_pool(execution_mod, monkeypatch, db)
    monkeypatch.setattr(
        execution_mod.truth, "execution_source_trusted", execution_source_trusted
    )

    with pytest.raises(execution_mod.OrchestratorExecutionError) as exc:
        await execution_mod.execute_engines(_user(), "orch-risk", {"engine_inputs": {}})

    assert (exc.value.status_code, exc.value.detail) == (
        409,
        "source_provenance_untrusted",
    )
    assert db.executions == []
    assert run["engine_plan"] == original_plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "source_id"),
    (("control_room_item", "manual-item"), ("wisdom_bit", "WB-TALENTO")),
)
async def test_untrusted_historical_operational_source_fails_before_writes(
    execution_mod, monkeypatch, source_type, source_id
) -> None:
    db = HistoricalManualExecutionDB()
    if source_type == "control_room_item":
        db.control_room_row = {
            "tenant_id": _user()["active_tenant_id"],
            "workspace_id": _user()["active_workspace_id"],
            "item_id": source_id,
            "item_kind": "anomaly",
            "metadata": {"source_type": "manual_fixture"},
        }
    run = db.add_run(candidate_engines=[{"name": "mpc_candidate"}])
    run.update(source_type=source_type, source_id=source_id)
    original_plan = dict(run["engine_plan"])
    _patch_pool(execution_mod, monkeypatch, db)
    monkeypatch.setattr(
        execution_mod.truth, "execution_source_trusted", execution_source_trusted
    )

    with pytest.raises(execution_mod.OrchestratorExecutionError) as exc:
        await execution_mod.execute_engines(_user(), "orch-risk", {"engine_inputs": {}})

    assert exc.value.status_code == 409
    assert db.executions == []
    assert run["engine_plan"] == original_plan

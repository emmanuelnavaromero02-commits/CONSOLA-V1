from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.routers.intelligence import CalibrationRecomputeRequest, MonteCarloRunRequest
from app.services.intelligence import backtesting, monte_carlo, orchestrator_execution
from app.services.intelligence.calibration_recompute_service import recompute


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _enable_engine_under_test(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MATH_ENGINES_ENABLED", "true")


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_cannot_choose_calibration_parent() -> None:
    with pytest.raises(ValidationError):
        CalibrationRecomputeRequest.model_validate(
            {
                "calibration_group": "source_type:replicon:margin:v1",
                "parent_calibration_group": "global:attacker:v1",
            }
        )


@pytest.mark.asyncio
async def test_direct_recompute_rejects_client_parent_before_db() -> None:
    with pytest.raises(HTTPException) as exc:
        await recompute(
            {},
            {
                "calibration_group": "source_type:replicon:margin:v1",
                "parent_calibration_group": "global:attacker:v1",
            },
        )
    assert exc.value.status_code == 422


def test_persisted_scoped_hierarchy_is_the_only_prior_source() -> None:
    service = _text(
        "console/app/services/intelligence/calibration_state_repository.py"
    ).lower()
    migration = _text("infra/init/99zzh_calibration_hierarchy_authority.sql").lower()
    assert "from calibration_group_hierarchy" in service
    assert "tenant_id is not distinct from" in service
    assert "explicit_parent_group" not in service
    assert "create table if not exists calibration_group_hierarchy" in migration
    assert "enable row level security" in migration
    parent_evidence = _text("infra/init/99zzjj_calibration_parent_evidence.sql").lower()
    assert "authoritative_calibration_parent_evidence" in parent_evidence
    assert "join public.calibration_group_hierarchy" in parent_evidence
    assert "provenance_status = 'verified'" in parent_evidence
    assert "durable_binary_evaluation" in parent_evidence


def test_orchestrator_only_reads_authoritative_calibration() -> None:
    service = _text("console/app/services/intelligence/orchestrator_execution.py")
    lowered = service.lower()
    assert "provenance_status = 'verified'" in lowered
    assert "authoritative_calibration_group = calibration_group" in lowered
    assert "durable_binary_evaluation_unavailable" not in service
    assert "legacy_unreviewed" not in service


def test_seed_and_discrete_values_are_bounded_at_public_contract() -> None:
    too_large = 2**63
    payload = {
        "source_type": "manual_fixture",
        "source_id": "fixture",
        "seed": too_large,
        "input_variables": {"x": {"type": "fixed", "value": 1}},
    }
    with pytest.raises(ValidationError):
        MonteCarloRunRequest.model_validate(payload)

    payload["seed"] = 1
    payload["input_variables"] = {
        "x": {"type": "discrete", "values": list(range(1001))}
    }
    with pytest.raises(ValidationError):
        MonteCarloRunRequest.model_validate(payload)


def test_seed_and_values_are_bounded_in_engine_too() -> None:
    with pytest.raises(monte_carlo.MonteCarloValidationError):
        monte_carlo.run_single_simulation(
            {
                "seed": 2**63,
                "iterations": 1,
                "input_variables": {"x": {"type": "fixed", "value": 1}},
                "output_metric": "delta",
            }
        )
    with pytest.raises(monte_carlo.MonteCarloValidationError):
        monte_carlo.validate_variables(
            {"x": {"type": "discrete", "values": list(range(1001))}}
        )


@pytest.mark.parametrize(
    "app_env", [None, "", "production", "prod", "staging", "unknown", "testing"]
)
def test_fixture_validation_is_closed_outside_local(
    monkeypatch: pytest.MonkeyPatch, app_env: str | None
) -> None:
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(HTTPException) as exc:
        backtesting._mode("fixture_validation")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("app_env", ["test", "local", "development"])
def test_fixture_validation_requires_explicit_local_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    assert backtesting._mode("fixture_validation") == "fixture_validation"


def test_benchmark_approval_has_db_constraint_and_refinement_guard() -> None:
    migration = _text(
        "infra/init_gold/38_sap_successfactors_benchmark_approval_constraint.sql"
    ).lower()
    runtime = _text("refinement/app/successfactors_talent_runtime_fallbacks.py").lower()
    assert "check" in migration
    assert "approved_by is not null" in migration
    assert "approved_at is not null" in migration
    assert re.search(r"approval_recorded_by_server\s*=\s*true", migration)
    assert re.search(r"approval_authorization_verified\s*=\s*true", migration)
    assert "false as benchmark_approval_valid" in runtime
    assert "'unreviewed' as benchmark_provenance_status" in runtime


def test_outcome_writer_is_server_owned_and_deduplicated() -> None:
    migration = _text("infra/init/99zzg_prediction_outcome_authority.sql").lower()
    persistence = _text("console/app/services/intelligence/persistence.py")
    assert "function public.record_prediction_outcome" in migration
    assert "security definer" in migration
    assert "omega_outcome_evaluator.v1" in migration
    assert "unique" in migration and "outcome_identity" in migration
    assert "record_server_owned_outcome(" in persistence


def test_calibration_writers_close_direct_dml_privileges() -> None:
    authority = _text("infra/init/99zzj_calibration_write_authority.sql").lower()
    recompute = _text("infra/init/99zzk_calibration_server_recompute.sql").lower()
    migration = authority + "\n" + recompute
    observation_service = _text(
        "console/app/services/intelligence/calibration_observation_service.py"
    ).lower()
    state_repository = _text(
        "console/app/services/intelligence/calibration_state_repository.py"
    ).lower()
    orchestrator = _text(
        "console/app/services/intelligence/orchestrator_execution.py"
    ).lower()

    assert "function public.record_calibration_observation" in migration
    assert "function public.upsert_calibration_state" in migration
    assert "security definer" in migration
    for table in (
        "prediction_outcomes",
        "calibration_observations",
        "calibration_states",
    ):
        assert re.search(
            rf"revoke insert, update, delete on public\.{table}\s+from",
            migration,
        )
    assert "record_calibration_observation($1::jsonb)" in observation_service
    assert "upsert_calibration_state($1::jsonb)" in state_repository
    assert "processed_total" in orchestrator
    assert "eligible_total" in orchestrator
    assert "p_payload->'prior'" not in recompute
    assert "p_payload->'posterior'" not in recompute
    assert "prediction_outcomes_id_seq" in recompute
    assert "calibration hierarchy unavailable" in recompute


@pytest.mark.asyncio
async def test_filtered_recompute_is_rejected_before_database() -> None:
    with pytest.raises(HTTPException) as exc:
        await recompute(
            {},
            {
                "calibration_group": "source_type:replicon:margin:v1",
                "source_type": "prediction_outcome",
                "source_id": "41",
            },
        )
    assert exc.value.status_code == 422


class _MixedCalibrationRows:
    def __init__(self) -> None:
        self.rows = [
            {"provenance_status": "verified"},
            {"provenance_status": "quarantined"},
        ]

    async def fetchrow(self, sql: str, *_params):
        lowered = sql.lower()
        assert "provenance_status = 'verified'" in lowered
        assert "provenance_reason =" in lowered
        assert "durable_binary_evaluation" in lowered
        if not any(row["provenance_status"] == "verified" for row in self.rows):
            return None
        return {
            "posterior": {"mean": 0.75, "alpha": 3, "beta": 1},
            "sample_count": 1,
            "confidence_score": 0.5,
            "metrics": {
                "sample_count": 1,
                "eligible_total": 1,
                "processed_total": 1,
                "skipped_total": 0,
                "complete": True,
                "provenance_complete": True,
                "binary_evaluation_complete": True,
            },
        }


async def _mixed_lookup(conn: _MixedCalibrationRows):
    return await orchestrator_execution._run_bayesian_lookup(
        conn,
        workspace_id="workspace-a",
        run={"source_type": "signal", "source_id": "signal-a"},
        engine_inputs={"bayesian_calibration": {"calibration_group": "group-a"}},
    )


@pytest.mark.asyncio
async def test_quarantined_row_never_influences_direct_orchestrator_lookup() -> None:
    conn = _MixedCalibrationRows()
    result = await _mixed_lookup(conn)
    assert result[0] == "succeeded"
    assert result[1]["sample_count"] == 1
    conn.rows[0]["provenance_status"] = "quarantined"
    blocked = await _mixed_lookup(conn)
    assert blocked[0] == "skipped"


def test_quarantined_row_never_influences_http_orchestrator_lookup() -> None:
    conn = _MixedCalibrationRows()
    app = FastAPI()

    @app.get("/lookup")
    async def lookup():
        result = await _mixed_lookup(conn)
        return {"status": result[0], "sample_count": result[1].get("sample_count")}

    response = TestClient(app).get("/lookup")
    assert response.json() == {"status": "succeeded", "sample_count": 1}

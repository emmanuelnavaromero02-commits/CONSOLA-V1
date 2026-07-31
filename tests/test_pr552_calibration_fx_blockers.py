from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.intelligence import CalibrationRecomputeRequest, MonteCarloRunRequest
from app.services.intelligence import backtesting, monte_carlo
from app.services.intelligence.calibration_recompute_service import recompute


ROOT = Path(__file__).resolve().parents[1]
PNL_SQL = (
    ROOT / "cartridges/replicon/datasets/pnl_mensual.sql",
    ROOT / "cartridges/replicon/datasets/pnl_detalle_consultor.sql",
    ROOT / "infra/init/10_replicon_gold_seed.sql",
    ROOT / "infra/init/65_replicon_mejoras_seed_refresh.sql",
)


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
    migration = _text("infra/init/99zzg_calibration_fx_truth.sql").lower()
    assert "from calibration_group_hierarchy" in service
    assert "tenant_id is not distinct from" in service
    assert "explicit_parent_group" not in service
    assert "create table if not exists calibration_group_hierarchy" in migration
    assert "enable row level security" in migration


def test_orchestrator_only_reads_authoritative_calibration() -> None:
    service = _text("console/app/services/intelligence/orchestrator_execution.py")
    lowered = service.lower()
    assert "provenance_status = 'verified'" in lowered
    assert "authoritative_calibration_group = calibration_group" in lowered
    assert "durable_binary_evaluation_unavailable" not in service
    assert "legacy_unreviewed" not in service


def test_all_eight_productive_fx_divisors_are_removed() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PNL_SQL)
    assert len(re.findall(r"/\s*20\.0\b", combined)) == 0
    for path in PNL_SQL:
        lowered = path.read_text(encoding="utf-8").lower()
        assert "missing_fx" in lowered
        assert "financial_status" in lowered
        assert "fx_source" in lowered
        assert "fx_observed_at" in lowered
        assert "original_currency" in lowered


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
    runtime = _text(
        "refinement/app/successfactors_talent_runtime_fallbacks.py"
    ).lower()
    assert "check" in migration
    assert "approved_by is not null" in migration
    assert "approved_at is not null" in migration
    assert "approval_recorded_by_server = true" in migration
    assert "approval_authorization_verified = true" in migration
    assert "false as benchmark_approval_valid" in runtime
    assert "'unreviewed' as benchmark_provenance_status" in runtime


def test_outcome_writer_is_server_owned_and_deduplicated() -> None:
    migration = _text("infra/init/99zzg_calibration_fx_truth.sql").lower()
    persistence = _text("console/app/services/intelligence/persistence.py")
    assert "create function record_prediction_outcome" in migration
    assert "security definer" in migration
    assert "omega_outcome_evaluator.v1" in migration
    assert "unique" in migration and "outcome_identity" in migration
    assert "record_server_owned_outcome(" in persistence

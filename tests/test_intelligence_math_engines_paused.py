from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.intelligence import (
    calibration_observation_service,
    calibration_recompute_service,
    decision_intelligence,
    engine,
    monte_carlo_service,
    orchestrator_execution,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "console/app/services/intelligence/engine_policy.py"
ROUTER = ROOT / "console/app/routers/intelligence.py"
RUNTIME = ROOT / "console/app/services/agent_runtime.py"
GOLD = ROOT / "console/app/services/intelligence/gold_control_room.py"
MCP_TOOL = ROOT / "mcp-infra/app/tools/control_room.py"
MONTE_CARLO_SERVICE = ROOT / "console/app/services/intelligence/monte_carlo_service.py"
CALIBRATION_OBSERVE = ROOT / "console/app/services/intelligence/calibration_observation_service.py"
CALIBRATION_RECOMPUTE = ROOT / "console/app/services/intelligence/calibration_recompute_service.py"
ORCHESTRATOR_EXECUTION = ROOT / "console/app/services/intelligence/orchestrator_execution.py"
INTELLIGENCE_ENGINE = ROOT / "console/app/services/intelligence/engine.py"
DECISION_INTELLIGENCE = ROOT / "console/app/services/intelligence/decision_intelligence.py"
CALIBRATION_AUTOPILOT = ROOT / "console/app/services/intelligence/calibration_autopilot.py"
TALENT_SIMULATION = ROOT / "console/app/services/intelligence/talent_retention_simulation.py"


def _load_policy():
    spec = importlib.util.spec_from_file_location("engine_policy_under_test", POLICY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_math_engines_are_paused_by_default_and_require_explicit_opt_in(
    monkeypatch,
) -> None:
    policy = _load_policy()
    monkeypatch.delenv(policy.ENV_NAME, raising=False)
    assert policy.math_engines_enabled() is False

    for false_value in ("", "0", "false", "no", "off", "unexpected"):
        monkeypatch.setenv(policy.ENV_NAME, false_value)
        assert policy.math_engines_enabled() is False

    for true_value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(policy.ENV_NAME, true_value)
        assert policy.math_engines_enabled() is True


def test_every_execution_surface_enforces_the_server_owned_pause() -> None:
    router = ROUTER.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    gold = GOLD.read_text(encoding="utf-8")
    mcp = MCP_TOOL.read_text(encoding="utf-8")

    assert router.count("_require_math_engines_enabled()") >= 6
    assert "if body.execute_engines:\n        _require_math_engines_enabled()" in router
    assert "manual Talent decision orchestration is paused by server policy" in router
    assert "not engine_policy.math_engines_enabled() and engine in" in runtime
    assert "and not _is_successfactors_talent_monitor(agent)" in runtime
    assert "if not engine_policy.math_engines_enabled():" in gold
    assert '"reason": engine_policy.PAUSED_REASON' in gold
    assert "execute_engines: bool = False" in mcp
    assert "if not engine_policy.math_engines_enabled():\n        return {}" in (
        INTELLIGENCE_ENGINE.read_text(encoding="utf-8")
    )
    assert "calibration_reason\": engine_policy.PAUSED_REASON" in (
        DECISION_INTELLIGENCE.read_text(encoding="utf-8")
    )


def test_service_boundaries_and_best_effort_paths_cannot_bypass_pause() -> None:
    for path in (
        MONTE_CARLO_SERVICE,
        CALIBRATION_OBSERVE,
        CALIBRATION_RECOMPUTE,
        ORCHESTRATOR_EXECUTION,
    ):
        source = path.read_text(encoding="utf-8")
        assert "if not engine_policy.math_engines_enabled():" in source, path
        assert "engine_policy.PAUSED_REASON" in source, path

    for path in (CALIBRATION_AUTOPILOT, TALENT_SIMULATION):
        source = path.read_text(encoding="utf-8")
        assert "if not engine_policy.math_engines_enabled():" in source, path
        assert '{"status": "paused", "reason": engine_policy.PAUSED_REASON}' in source


@pytest.mark.asyncio
async def test_paused_service_boundaries_reject_before_database_access(
    monkeypatch,
) -> None:
    monkeypatch.delenv("INTELLIGENCE_MATH_ENGINES_ENABLED", raising=False)
    monte_pool = AsyncMock()
    calibration_pool = AsyncMock()
    recompute_pool = AsyncMock()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", monte_pool)
    monkeypatch.setattr(calibration_observation_service.auth, "pool", calibration_pool)
    monkeypatch.setattr(calibration_recompute_service.auth, "pool", recompute_pool)

    with pytest.raises(HTTPException) as monte_error:
        await monte_carlo_service.run_simulation({}, {})
    with pytest.raises(HTTPException) as calibration_error:
        await calibration_observation_service.observe({}, {})
    with pytest.raises(HTTPException) as recompute_error:
        await calibration_recompute_service.recompute({}, {})
    with pytest.raises(orchestrator_execution.OrchestratorExecutionError) as orchestration_error:
        await orchestrator_execution.execute_engines({}, "not-loaded", {})

    assert (monte_error.value.status_code, monte_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (calibration_error.value.status_code, calibration_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (recompute_error.value.status_code, recompute_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (
        orchestration_error.value.status_code,
        orchestration_error.value.detail,
    ) == (403, "paused_by_server_policy")
    monte_pool.assert_not_awaited()
    calibration_pool.assert_not_awaited()
    recompute_pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_off_never_loads_or_applies_historical_calibration(
    monkeypatch,
) -> None:
    monkeypatch.delenv("INTELLIGENCE_MATH_ENGINES_ENABLED", raising=False)
    state_loader = AsyncMock(
        return_value={"risk:late": {"posterior": {"mean": 1.0}}}
    )
    monkeypatch.setattr(
        engine.calibration_service,
        "get_state_map_for_live_calibration",
        state_loader,
    )
    states = await engine._load_live_calibration_states(
        {},
        [{"cartridge": "hubspot", "metrics": [{"id": "late"}]}],
        set(),
    )
    assert states == {}
    state_loader.assert_not_awaited()

    apply_calibration = AsyncMock(side_effect=AssertionError("must not apply history"))
    monkeypatch.setattr(
        decision_intelligence.calibration,
        "apply_calibration_to_probability",
        apply_calibration,
    )
    result = decision_intelligence._calibration_for_probability(
        0.42,
        calibration_state={
            "calibration_group": "risk:late",
            "posterior": {"mean": 1.0},
            "metrics": {"sample_count": 1_000, "confidence_score": 1.0},
        },
        calibration_group="risk:late",
    )
    assert result["raw_probability"] == 0.42
    assert result["calibrated_probability"] == 0.42
    assert result["calibration_applied"] is False
    assert result["calibration_reason"] == "paused_by_server_policy"
    assert result["sample_count"] == 0
    apply_calibration.assert_not_called()

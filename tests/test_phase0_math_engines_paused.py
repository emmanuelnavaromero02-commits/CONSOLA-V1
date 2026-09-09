from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.domains.agentops.successfactors_talent_monitor import (
    successfactors_talent_monitor_contract,
)
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
RUNTIME = ROOT / "console/app/services/agent_runtime.py"
ROUTER = ROOT / "console/app/routers/intelligence.py"
MCP_TOOL = ROOT / "mcp-infra/app/tools/control_room.py"
COMPOSE = ROOT / "infra/docker-compose.yml"
TALENT_MONITOR = (
    ROOT / "console/app/domains/agentops/successfactors_talent_monitor.py"
)


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

    for value in ("", "0", "false", "no", "off", "unexpected"):
        monkeypatch.setenv(policy.ENV_NAME, value)
        assert policy.math_engines_enabled() is False

    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(policy.ENV_NAME, value)
        assert policy.math_engines_enabled() is True


def test_scheduled_monitor_skips_every_paused_engine_and_uses_no_forged_arg() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    section = source.split("async def run_scheduled_monitor", 1)[1]
    pause_section = section.split("if spec.get(\"enabled\") is False", 1)[0]
    monte_carlo_args = section.split(
        'result = await _call(\n                    "mcp-infra__simulation__monte_carlo_run"',
        1,
    )[1].split("engine_results.append", 1)[0]

    for engine_name in (
        "monte_carlo",
        "bayesian_calibration",
        "minimax",
        "decision_orchestrator",
    ):
        assert f'"{engine_name}"' in pause_section
    assert "engine_policy.PAUSED_REASON" in pause_section
    assert '"model_version"' not in monte_carlo_args
    assert "execute_engines\": bool(\n                                engine_policy.math_engines_enabled()" in section


def test_public_execution_surfaces_enforce_server_owned_pause() -> None:
    router = ROUTER.read_text(encoding="utf-8")
    mcp = MCP_TOOL.read_text(encoding="utf-8")

    assert router.count("_require_math_engines_enabled()") >= 6
    assert "execute_engines: bool = False" in mcp
    assert (
        "INTELLIGENCE_MATH_ENGINES_ENABLED: "
        "${INTELLIGENCE_MATH_ENGINES_ENABLED:-false}"
    ) in COMPOSE.read_text(encoding="utf-8")
    assert "if not engine_policy.math_engines_enabled():\n        return {}" in (
        ROOT / "console/app/services/intelligence/engine.py"
    ).read_text(encoding="utf-8")
    assert "calibration_reason\": engine_policy.PAUSED_REASON" in (
        ROOT / "console/app/services/intelligence/decision_intelligence.py"
    ).read_text(encoding="utf-8")


def test_talent_monitor_contract_cannot_reenable_paused_engines() -> None:
    source = TALENT_MONITOR.read_text(encoding="utf-8")
    _, _, extra = successfactors_talent_monitor_contract()
    engines = extra["monitor"]["engines"]

    assert len(engines) == 3
    assert all(engine["enabled"] is False for engine in engines)
    assert all(engine["reason"] == "paused_by_server_policy" for engine in engines)
    assert engines[2]["execute_engines"] is False
    assert '"model_version": "wb-talento.monitor.v2"' not in source


@pytest.mark.asyncio
async def test_paused_services_reject_before_database_access(monkeypatch) -> None:
    monkeypatch.delenv("INTELLIGENCE_MATH_ENGINES_ENABLED", raising=False)
    monte_pool = AsyncMock()
    observe_pool = AsyncMock()
    recompute_pool = AsyncMock()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", monte_pool)
    monkeypatch.setattr(calibration_observation_service.auth, "pool", observe_pool)
    monkeypatch.setattr(calibration_recompute_service.auth, "pool", recompute_pool)

    with pytest.raises(HTTPException) as monte_error:
        await monte_carlo_service.run_simulation({}, {})
    with pytest.raises(HTTPException) as observe_error:
        await calibration_observation_service.observe({}, {})
    with pytest.raises(HTTPException) as recompute_error:
        await calibration_recompute_service.recompute({}, {})
    with pytest.raises(orchestrator_execution.OrchestratorExecutionError) as orch_error:
        await orchestrator_execution.execute_engines({}, "missing", {})

    assert (monte_error.value.status_code, monte_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (observe_error.value.status_code, observe_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (recompute_error.value.status_code, recompute_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    assert (orch_error.value.status_code, orch_error.value.detail) == (
        403,
        "paused_by_server_policy",
    )
    monte_pool.assert_not_awaited()
    observe_pool.assert_not_awaited()
    recompute_pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_policy_ignores_historical_calibration(monkeypatch) -> None:
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
    result = decision_intelligence._calibration_for_probability(
        0.42,
        calibration_state={
            "posterior": {"mean": 1.0},
            "metrics": {"sample_count": 1000, "confidence_score": 1.0},
        },
        calibration_group="risk:late",
    )

    assert states == {}
    state_loader.assert_not_awaited()
    assert result["calibrated_probability"] == 0.42
    assert result["calibration_applied"] is False
    assert result["calibration_reason"] == "paused_by_server_policy"

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_service


REPO = Path(__file__).resolve().parents[1]


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, str, tuple]] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql: str, *params):
        self.calls.append(("execute", sql, params))

    async def fetchval(self, sql: str, *params):
        self.calls.append(("fetchval", sql, params))
        if "to_regclass('public.backtest_results')" in sql:
            return "backtest_results"
        if any(
            table in sql
            for table in (
                "monte_carlo_simulations",
                "decision_options",
                "prediction_outcomes",
                "backtest_results",
            )
        ):
            return 1
        return None

    async def fetchrow(self, sql: str, *params):
        self.calls.append(("fetchrow", sql, params))
        if "SELECT 1 AS trusted FROM control_room_items" in sql:
            return {"trusted": 1}
        if "SELECT source_type, source_id" in sql and "monte_carlo_simulations" in sql:
            return {"source_type": "wisdom_bit", "source_id": "WB-TALENTO"}
        if "SELECT *" in sql and "FROM calibration_states" in sql:
            return None
        if "INSERT INTO calibration_observations" in sql:
            return {
                "id": 1,
                "observation_id": params[0],
                "tenant_id": params[1],
                "workspace_id": params[2],
                "source_type": params[3],
                "source_id": params[4],
                "predicted_metric": params[5],
                "actual_status": params[10],
                "reproducibility_hash": params[20],
            }
        if "INSERT INTO calibration_states" in sql:
            return {
                "id": 1,
                "state_id": params[0],
                "tenant_id": params[1],
                "workspace_id": params[2],
                "calibration_group": params[3],
                "model_version": params[4],
                "sample_count": params[8],
                "hit_count": params[9],
                "confidence_score": params[18],
                "reproducibility_hash": params[20],
            }
        return None

    async def fetch(self, sql: str, *params):
        self.calls.append(("fetch", sql, params))
        return []


class _Acquire:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConnection()

    def acquire(self):
        return _Acquire(self.conn)


def _payload():
    return {
        "source_type": "manual_fixture",
        "source_id": "fixture-a",
        "predicted_metric": "net_value",
        "predicted_probability": 0.8,
        "predicted_value": 100.0,
        "predicted_interval": {"low": 80.0, "high": 120.0},
        "actual_value": 110.0,
        "actual_status": "hit",
        "calibration_group": "monte_carlo",
    }


def test_calibration_router_adds_new_endpoints_without_replacing_legacy_report():
    router = (REPO / "console/app/routers/intelligence.py").read_text(encoding="utf-8")

    assert '"/calibration/observe"' in router
    assert '"/calibration/recompute"' in router
    assert '"/calibration/state"' in router
    assert '@internal_router.post("/calibration/state")' in router
    assert '"/calibration/observations"' in router
    assert "CalibrationObservationRequest(_StrictModel)" in router
    assert "CalibrationRecomputeRequest(_StrictModel)" in router
    assert "CalibrationStateRequest(_StrictModel)" in router
    assert (
        '_internal_mcp_user(body, internal_service, permission="datasets.read")'
        in router
    )
    assert "parent_calibration_group" in router
    assert "await intelligence_history.calibration_report" in router
    assert "await calibration_service.observe" in router
    assert "await calibration_service.recompute" in router
    assert "Depends(require_csrf)" in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert 'Depends(require_permission("datasets.read"))' in router


def test_calibration_service_uses_scoped_db_and_blocks_scope_payloads():
    service = "\n".join(
        (REPO / "console/app/services/intelligence" / name).read_text(encoding="utf-8")
        for name in (
            "calibration_service.py",
            "calibration_observation_service.py",
            "calibration_state_repository.py",
            "calibration_source_validation.py",
            *"source_provenance.py source_provenance_policy.py calibration_recompute_batch.py".split(),
            "calibration_recompute_service.py",
            "calibration_validation_service.py",
        )
    )
    assert "from app.services.db_scope import scoped_db_for_user" in service
    assert "async with scoped_db_for_user(pool, user)" in service
    assert "get_state_map_for_live_calibration" in service
    assert "derive_partial_pooling_prior" in service
    assert "tenant_id" in service
    assert "workspace_id" in service
    assert "security_context" in service
    assert '"scenario_assumption"' in service
    assert "FROM backtest_results" in service

    with pytest.raises(HTTPException):
        calibration_service._validate_payload({**_payload(), "workspace_id": "ws-b"})
    with pytest.raises(HTTPException):
        calibration_service._validate_payload(
            {
                **_payload(),
                "evidence_refs": [
                    {"type": "row", "id": "1", "tenant_id": "tenant-b"},
                ],
            }
        )


def test_calibration_accepts_market_context_evidence_refs():
    clean = calibration_service._validate_payload(
        {
            **_payload(),
            "evidence_refs": [
                {
                    "type": "market_context",
                    "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
                }
            ],
        }
    )

    assert clean["evidence_refs"] == [
        {
            "type": "market_context",
            "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
        }
    ]


@pytest.mark.parametrize(
    "app_env", [None, "", "production", "prod", "staging", "unknown", "dev", "testing"]
)
def test_manual_fixture_rejects_nonlocal_env_even_with_synthetic_flag(
    monkeypatch, app_env
):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("CALIBRATION_ALLOW_SYNTHETIC", "true")
    with pytest.raises(HTTPException) as exc:
        calibration_service._validate_payload(
            {**_payload(), "source_type": "manual_fixture", "source_id": "fixture"}
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("app_env", ["test", "local", "development"])
def test_manual_fixture_requires_exact_explicit_local_env(monkeypatch, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("CALIBRATION_ALLOW_SYNTHETIC", raising=False)
    clean = calibration_service._validate_payload(
        {**_payload(), "source_type": "manual_fixture", "source_id": "fixture"}
    )
    assert clean["source_type"] == "manual_fixture"


@pytest.mark.asyncio
async def test_observe_sets_scope_validates_source_and_persists(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(calibration_service.auth, "pool", AsyncMock(return_value=fake))

    result = await calibration_service.observe(
        {"id": 42, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        _payload(),
    )

    assert result["observation"]["observation_id"].startswith("cal-obs-")
    assert result["state"]["state_id"].startswith("cal-state-")
    assert fake.conn.calls[0][0] == "execute"
    assert "set_config('app.tenant_id'" in fake.conn.calls[0][1]
    assert not any("monte_carlo_simulations" in call[1] for call in fake.conn.calls)
    assert any(
        call[0] == "fetchrow" and "INSERT INTO calibration_observations" in call[1]
        for call in fake.conn.calls
    )
    assert any(
        call[0] == "fetchrow" and "INSERT INTO calibration_states" in call[1]
        for call in fake.conn.calls
    )


def test_calibration_migration_is_scoped_and_does_not_relax_rls():
    sql = (REPO / "infra/init/99r_bayesian_calibration.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS calibration_observations" in sql
    assert "CREATE TABLE IF NOT EXISTS calibration_states" in sql
    for column in (
        "tenant_id",
        "workspace_id",
        "source_type",
        "predicted_probability",
        "actual_status",
        "posterior",
        "confidence_score",
        "reproducibility_hash",
    ):
        assert column in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER ROLE omega_console NOBYPASSRLS" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert (
        "GRANT SELECT, INSERT, UPDATE ON calibration_observations TO omega_console"
        in sql
    )
    assert "GRANT SELECT, INSERT, UPDATE ON calibration_states TO omega_console" in sql


def test_makefile_exposes_bayesian_calibration_aws_probe():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    script = REPO / "scripts/aws_bayesian_calibration_probe.py"
    loop_script = REPO / "scripts/aws_bayesian_loop_probe.py"
    local_loop_script = REPO / "scripts/bayesian_loop_probe.py"

    assert "bayesian-calibration-aws-probe" in makefile
    assert "bayesian-loop-probe" in makefile
    assert "bayesian-loop-probe-aws" in makefile
    assert script.exists()
    assert loop_script.exists()
    assert local_loop_script.exists()
    assert "OMEGA_BAYESIAN_CALIBRATION_CHECK" in script.read_text(encoding="utf-8")
    assert "OMEGA_BAYESIAN_LOOP_CHECK" in loop_script.read_text(encoding="utf-8")

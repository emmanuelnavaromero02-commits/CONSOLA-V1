from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.intelligence import market_context
from app.services.intelligence import monte_carlo_service


REPO = Path(__file__).resolve().parents[1]


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, str, tuple]] = []
        self.simulation: dict | None = None

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
        if "intelligence_signals" in sql:
            return 1
        if "to_regclass" in sql:
            return "backtest_runs"
        return None

    async def fetchrow(self, sql: str, *params):
        self.calls.append(("fetchrow", sql, params))
        if "SELECT *" in sql and "monte_carlo_simulations" in sql:
            return self.simulation
        self.simulation = {
            "id": 1,
            "simulation_id": params[0],
            "tenant_id": params[1],
            "workspace_id": params[2],
            "source_type": params[3],
            "source_id": params[4],
            "horizon_days": params[5],
            "iterations": params[6],
            "seed": params[7],
            "model_version": params[8],
            "output_metric": params[11],
            "reproducibility_hash": params[18],
        }
        return self.simulation

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
        "source_type": "signal",
        "source_id": "signal-a",
        "iterations": 20,
        "seed": 7,
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": 100},
            "expected_delta": {"type": "fixed", "value": 10},
        },
        "output_metric": "net_value",
    }


def test_monte_carlo_router_exposes_scoped_endpoints_and_csrf_guard():
    router = (REPO / "console/app/routers/intelligence.py").read_text(encoding="utf-8")

    assert '"/monte-carlo/run"' in router
    assert '"/monte-carlo/{simulation_id}"' in router
    assert '@router.get("/monte-carlo"' in router
    assert '@v1_router.get(\n    "/monte-carlo"' in router
    assert "MonteCarloRunRequest(_StrictModel)" in router
    assert "use_external_market_context" in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert 'Depends(require_permission("datasets.read"))' in router
    assert "Depends(require_csrf)" in router
    assert "tenant_id" in router and "scope variables are not accepted" in router


def test_monte_carlo_service_uses_scoped_db_and_blocks_scope_payloads():
    service = (
        REPO / "console/app/services/intelligence/monte_carlo_service.py"
    ).read_text(encoding="utf-8")

    assert "from app.services.db_scope import scoped_db_for_user" in service
    assert "resolve_market_context_inputs" in service
    assert "async with scoped_db_for_user(pool, user)" in service
    assert "tenant_id" in service
    assert "security_context" in service

    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload({**_payload(), "workspace_id": "ws-b"})
    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload(
            {
                **_payload(),
                "input_variables": {
                    "tenant_id": {"type": "fixed", "value": 1},
                },
            }
        )
    with pytest.raises(HTTPException):
        monte_carlo_service._validate_payload(
            {
                **_payload(),
                "options": [
                    {
                        "option_id": "bad",
                        "input_variables": {
                            "security_context": {"type": "fixed", "value": 1},
                        },
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_market_context_variable_requires_explicit_opt_in():
    payload = {
        **_payload(),
        "input_variables": {
            "cost_per_day": {
                "type": "external_market_context",
                "metric_name": "usd_mxn_fix",
            }
        },
    }

    with pytest.raises(HTTPException) as exc:
        await market_context.resolve_market_context_inputs(payload, {"id": 1})

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_market_context_variable_resolves_to_distribution(monkeypatch):
    async def fake_query(dataset, user, limit=200):
        assert dataset == "banxico_market_context"
        return [
            {
                "metric_name": "usd_mxn_fix",
                "value": "18.50",
                "unit": "mxn_per_usd",
                "as_of": "2026-07-10",
                "usable": True,
                "freshness_status": "ready",
                "confidence": "0.95",
                "source_authority": "Banxico",
                "source_host": "www.banxico.org.mx",
                "payload_hash": "abc123payload",
                "request_hash": "req123",
            }
        ]

    monkeypatch.setattr(market_context, "query_gold_dataset_rows", fake_query)
    clean = await market_context.resolve_market_context_inputs(
        {
            **_payload(),
            "use_external_market_context": True,
            "input_variables": {
                "cost_per_day": {
                    "type": "external_market_context",
                    "metric_name": "usd_mxn_fix",
                    "uncertainty_pct": "0.10",
                }
            },
        },
        {"id": 1, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
    )

    variable = clean["input_variables"]["cost_per_day"]
    assert variable["type"] == "triangular"
    assert variable["low"] == 16.65
    assert variable["mode"] == 18.5
    assert variable["high"] == 20.35
    assert clean["evidence_refs"][0]["type"] == "market_context"
    assumption = clean["assumptions"]["external_market_context"][0]
    assert assumption["metric_name"] == "usd_mxn_fix"
    assert assumption["unit"] == "mxn_per_usd"
    assert "source_url" not in assumption
    assert "token" not in assumption


@pytest.mark.asyncio
async def test_market_context_variable_rejects_low_confidence(monkeypatch):
    async def fake_query(dataset, user, limit=200):
        assert dataset == "banxico_market_context"
        return [
            {
                "metric_name": "usd_mxn_fix",
                "value": "18.50",
                "as_of": "2026-07-10",
                "usable": True,
                "freshness_status": "ready",
                "confidence": "0.79",
            }
        ]

    monkeypatch.setattr(market_context, "query_gold_dataset_rows", fake_query)

    with pytest.raises(HTTPException) as exc:
        await market_context.resolve_market_context_inputs(
            {
                **_payload(),
                "use_external_market_context": True,
                "input_variables": {
                    "cost_per_day": {
                        "type": "external_market_context",
                        "metric_name": "usd_mxn_fix",
                    }
                },
            },
            {"id": 1, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        )

    assert exc.value.status_code == 422
    assert "confidence is too low" in str(exc.value.detail)


def test_mcp_monte_carlo_tool_exposes_external_market_opt_in():
    source = (REPO / "mcp-infra/app/tools/control_room.py").read_text(encoding="utf-8")

    assert "use_external_market_context" in source
    assert '"use_external_market_context": bool(use_external_market_context)' in source


def test_manual_fixture_is_disabled_in_production_without_explicit_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MONTE_CARLO_ALLOW_SYNTHETIC", raising=False)

    with pytest.raises(HTTPException) as exc:
        monte_carlo_service._validate_payload(
            {
                **_payload(),
                "source_type": "manual_fixture",
                "source_id": "fixture",
            }
        )
    assert exc.value.status_code == 403

    monkeypatch.setenv("MONTE_CARLO_ALLOW_SYNTHETIC", "true")
    clean = monte_carlo_service._validate_payload(
        {**_payload(), "source_type": "manual_fixture", "source_id": "fixture"}
    )
    assert clean["source_type"] == "manual_fixture"


def test_wisdom_bit_source_is_allowlisted_without_synthetic_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MONTE_CARLO_ALLOW_SYNTHETIC", raising=False)

    clean = monte_carlo_service._validate_payload(
        {**_payload(), "source_type": "wisdom_bit", "source_id": "WB-TALENTO"}
    )

    assert clean["source_type"] == "wisdom_bit"
    assert clean["source_id"] == "WB-TALENTO"


@pytest.mark.asyncio
async def test_run_simulation_sets_db_scope_validates_source_and_persists(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", AsyncMock(return_value=fake))

    result = await monte_carlo_service.run_simulation(
        {"id": 42, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        _payload(),
    )

    assert result["simulation"]["simulation_id"].startswith("mc-")
    assert fake.conn.calls[0][0] == "execute"
    assert "set_config('app.tenant_id'" in fake.conn.calls[0][1]
    assert any(
        call[0] == "fetchval" and "intelligence_signals" in call[1]
        for call in fake.conn.calls
    )
    assert any(
        call[0] == "fetchrow" and "INSERT INTO monte_carlo_simulations" in call[1]
        for call in fake.conn.calls
    )
    assert any(
        call[0] == "fetchrow" and "SELECT *" in call[1]
        and "monte_carlo_simulations" in call[1]
        for call in fake.conn.calls
    )


@pytest.mark.asyncio
async def test_run_simulation_accepts_wisdom_bit_source_without_signal_lookup(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", AsyncMock(return_value=fake))

    result = await monte_carlo_service.run_simulation(
        {"id": 42, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        {**_payload(), "source_type": "wisdom_bit", "source_id": "WB-TALENTO"},
    )

    assert result["simulation"]["source_type"] == "wisdom_bit"
    assert not any(
        call[0] == "fetchval" and "intelligence_signals" in call[1]
        for call in fake.conn.calls
    )
    assert any(
        call[0] == "fetchrow" and "INSERT INTO monte_carlo_simulations" in call[1]
        for call in fake.conn.calls
    )


def test_monte_carlo_migration_is_scoped_and_does_not_relax_rls():
    sql = (
        REPO / "infra/init/99q_monte_carlo_simulations.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS monte_carlo_simulations" in sql
    for column in (
        "tenant_id",
        "workspace_id",
        "simulation_id",
        "source_type",
        "input_variables",
        "distribution_summary",
        "sensitivity",
        "option_comparison",
        "reproducibility_hash",
    ):
        assert column in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER ROLE omega_console NOBYPASSRLS" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "'wisdom_bit'" in sql
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert "GRANT SELECT, INSERT, UPDATE ON monte_carlo_simulations TO omega_console" in sql


def test_monte_carlo_wisdom_bit_source_migration_preserves_rls():
    sql = (
        REPO / "infra/init/99z_monte_carlo_wisdom_bit_source.sql"
    ).read_text(encoding="utf-8")

    assert "monte_carlo_simulations_source_type_check" in sql
    assert "'wisdom_bit'" in sql
    assert "ALTER TABLE monte_carlo_simulations" in sql
    assert "ENABLE ROW LEVEL SECURITY" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql

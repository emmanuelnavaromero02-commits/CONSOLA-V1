from __future__ import annotations

import asyncio
from pathlib import Path

from app.domains.agentops.successfactors_talent_monitor import (
    SUCCESSFACTORS_TALENT_MONITOR_SLUG,
    coerce_successfactors_talent_monitor_payload,
    ensure_successfactors_talent_monitor,
    successfactors_talent_monitor_contract,
    successfactors_talent_monitor_needs_runtime_repair,
    sync_agentops_monitor_candidates,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_successfactors_talent_monitor_contract_uses_operational_inputs():
    allowed_tools, rag_filter, extra = successfactors_talent_monitor_contract()

    assert "mcp-infra__wisdom_bits__run" in allowed_tools
    assert "mcp-infra__control_room__talent_9box_read" in allowed_tools
    assert "mcp-infra__search_rag" in allowed_tools
    assert "mcp-infra__control_room__raise_analysis_alert" not in allowed_tools
    assert "refinement__query_dataset" not in allowed_tools
    assert "mcp-infra__simulation__monte_carlo_run" not in allowed_tools
    assert "mcp-infra__calibration__bayesian_state" not in allowed_tools
    assert rag_filter == {
        "cartridges": ["sap_successfactors"],
        "kinds": ["schema"],
    }
    monitor = extra["monitor"]
    assert monitor["wisdom_bit_id"] == "WB-TALENTO"
    assert monitor["dataset"] == "sap_successfactors_talent_operational_features"
    assert monitor["recommendation_only"] is True
    assert monitor["engines"][0]["enabled"] is False
    assert monitor["engines"][1]["enabled"] is False
    assert monitor["engines"][2]["execute_engines"] is False
    engine_inputs = monitor["engines"][2]["engine_inputs"]
    assert (
        engine_inputs["monte_carlo"]["input_dataset"]
        == "sap_successfactors_talent_simulation_inputs"
    )
    assert (
        engine_inputs["bayesian_calibration"]["calibration_group"]
        == "sap_successfactors:talent_readiness"
    )


def test_successfactors_talent_monitor_defers_legacy_alert_publication():
    source = (
        REPO_ROOT / "console/app/services/agent_runtime.py"
    ).read_text(encoding="utf-8")
    section = source.split("should_alert = _monitor_should_alert", 1)[1].split(
        "signal_count = _monitor_signal_count", 1
    )[0]

    assert 'alert_state = "grounded_required" if should_alert else "not_required"' in section
    assert "control_room__raise_analysis_alert" not in section


def test_successfactors_talent_monitor_candidates_require_workspace_scope():
    agents = [
        {
            "is_active": True,
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "extra": {"role": "monitor", "monitor": {"dataset": "x"}},
            "allowed_tools": ["infra__wisdom_bits__run"],
        },
        {
            "is_active": True,
            "tenant_id": "",
            "workspace_id": "workspace-a",
            "extra": {"role": "monitor", "monitor": {"dataset": "x"}},
            "allowed_tools": ["infra__wisdom_bits__run"],
        },
        {
            "is_active": True,
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "extra": {"role": "assistant", "monitor": {"dataset": "x"}},
            "allowed_tools": ["infra__wisdom_bits__run"],
        },
    ]

    assert sync_agentops_monitor_candidates(agents) == [agents[0]]


def test_successfactors_talent_monitor_payload_is_coerced_only_for_wb_talento():
    unrelated = {"cartridge_id": "hubspot", "slug": SUCCESSFACTORS_TALENT_MONITOR_SLUG}
    assert coerce_successfactors_talent_monitor_payload(unrelated) is unrelated

    payload = {
        "cartridge_id": "sap_successfactors",
        "slug": SUCCESSFACTORS_TALENT_MONITOR_SLUG,
        "extra": {"role": "assistant"},
        "allowed_tools": ["custom_tool"],
    }
    patched = coerce_successfactors_talent_monitor_payload(payload)

    assert patched is not payload
    assert patched["role"] == "monitor"
    assert patched["extra"]["role"] == "monitor"
    assert patched["extra"]["monitor"]["wisdom_bit_id"] == "WB-TALENTO"
    assert "custom_tool" not in patched["allowed_tools"]
    assert "mcp-infra__wisdom_bits__run" in patched["allowed_tools"]
    assert patched["model"] == "claude-sonnet-4-6"
    assert patched["temperature"] == 0.0


def test_successfactors_talent_monitor_runtime_repair_detects_missing_contract():
    assert successfactors_talent_monitor_needs_runtime_repair(
        {
            "cartridge_id": "sap_successfactors",
            "slug": SUCCESSFACTORS_TALENT_MONITOR_SLUG,
            "extra": {"role": "monitor"},
        }
    )
    tools, rag_filter, extra = successfactors_talent_monitor_contract()
    assert not successfactors_talent_monitor_needs_runtime_repair(
        {
            "cartridge_id": "sap_successfactors",
            "slug": SUCCESSFACTORS_TALENT_MONITOR_SLUG,
            "allowed_tools": tools,
            "rag_filter": rag_filter,
            "temperature": 0.0,
            "extra": extra,
        }
    )


def test_successfactors_talent_monitor_repair_detects_old_math_contract():
    tools, rag_filter, extra = successfactors_talent_monitor_contract()
    unsafe_extra = {**extra, "monitor": {**extra["monitor"]}}
    unsafe_extra["monitor"]["engines"] = [
        {**engine, "enabled": True} if engine["name"] == "monte_carlo" else engine
        for engine in extra["monitor"]["engines"]
    ]
    assert successfactors_talent_monitor_needs_runtime_repair(
        {
            "cartridge_id": "sap_successfactors",
            "slug": SUCCESSFACTORS_TALENT_MONITOR_SLUG,
            "allowed_tools": [*tools, "refinement__query_dataset"],
            "rag_filter": rag_filter,
            "temperature": 0.2,
            "extra": unsafe_extra,
        }
    )


def test_ensure_successfactors_talent_monitor_scopes_update_and_insert():
    class _AsyncContext:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def __init__(self):
            self.calls = []

        def transaction(self):
            return _AsyncContext()

        async def execute(self, sql, *args):
            self.calls.append((sql, args))
            if "UPDATE agents" in sql:
                return "UPDATE 0"
            return "OK"

    class _Pool:
        def __init__(self):
            self.conn = _Conn()

        def acquire(self):
            return _AsyncContext(self.conn)

    pool = _Pool()

    async def _get_pool():
        return pool

    def _ctx(_user):
        return {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
        }

    class _Logger:
        def warning(self, *_args, **_kwargs):
            raise AssertionError("unexpected warning")

    asyncio.run(
        ensure_successfactors_talent_monitor(
            {"id": 1},
            get_db_pool=_get_pool,
            build_security_context=_ctx,
            logger=_Logger(),
        )
    )

    assert len(pool.conn.calls) == 3
    assert "set_config('app.tenant_id'" in pool.conn.calls[0][0]
    assert pool.conn.calls[0][1] == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    assert "UPDATE agents" in pool.conn.calls[1][0]
    assert SUCCESSFACTORS_TALENT_MONITOR_SLUG in pool.conn.calls[1][1]
    assert "INSERT INTO agents" in pool.conn.calls[2][0]

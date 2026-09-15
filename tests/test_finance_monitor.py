"""Mission 4 — the Finance AgentOps monitor contract.

Mould: tests/test_agentops_successfactors_monitor_module.py, which pins the Talent
contract field by field rather than asserting it merely "looks like" a monitor.
The facts pinned here are the ones the runtime actually reads, and each has a
failure mode that is silent rather than loud, which is why it is pinned:

* a missing ``wisdom_bit_id`` makes the Finance monitor run the TALENT wisdom bit
  (``agent_runtime`` falls back to the literal ``"WB-TALENTO"``);
* a missing ``domain`` files Finance alerts under ``"Recursos Humanos"``;
* an engine left ``enabled`` that cannot run reports ``blocked``/``error``, and any
  such result makes ``monitor_should_alert`` return False — so the monitor would
  go permanently quiet instead of failing visibly.
"""

from __future__ import annotations

from app.domains.agentops.domain_monitor_support import (
    DECISION_ORCHESTRATOR_DISABLED_REASON,
    MONTE_CARLO_DISABLED_REASON,
)
from app.domains.agentops.finance_monitor import (
    FINANCE_MEMORY_SUBJECTS,
    FINANCE_MONITOR_CARTRIDGE,
    FINANCE_MONITOR_SLUG,
    FINANCE_MONITOR_SPEC,
    FINANCE_WISDOM_BIT_ID,
    finance_monitor_contract,
    finance_monitor_needs_runtime_repair,
    is_finance_monitor_row,
)
from app.services.monitor_alert_policy import monitor_should_alert


def _contract():
    return finance_monitor_contract()


def test_identity_matches_the_conversational_agent_it_shadows() -> None:
    assert FINANCE_MONITOR_CARTRIDGE == "sap_s4hana"
    # The slug is the conversational Controller Financiero's slug plus _monitor,
    # so the pairing is visible in the agents table without a lookup table.
    assert FINANCE_MONITOR_SLUG == "sap_s4hana_controller_financiero_monitor"
    assert FINANCE_MONITOR_SLUG.removesuffix("_monitor") == (
        "sap_s4hana_controller_financiero"
    )
    assert FINANCE_WISDOM_BIT_ID == "WB-FINANZAS"


def test_contract_returns_the_same_three_tuple_shape_as_talent() -> None:
    allowed_tools, rag_filter, extra = _contract()
    assert isinstance(allowed_tools, list) and allowed_tools
    assert rag_filter == {
        "cartridges": ["sap_s4hana"],
        "kinds": ["document", "schema"],
    }
    assert extra["role"] == "monitor"
    assert extra["category"] == "control_room"
    assert extra["scope"] == "workspace"
    assert extra["variables"] == {}


def test_allowed_tools_grant_the_domain_read_and_the_chain() -> None:
    allowed_tools, _, _ = _contract()
    # Its own Mission 2 aggregate read comes first: it is why the monitor exists.
    assert allowed_tools[0] == "mcp-infra__control_room__finance_kpis_read"
    # The deterministic chain always calls these two; without them the monitor
    # cannot run at all and sync_agentops_monitor_candidates would skip it.
    assert "mcp-infra__wisdom_bits__run" in allowed_tools
    assert "mcp-infra__control_room__raise_analysis_alert" in allowed_tools
    for tool in (
        "mcp-infra__decision__orchestrate",
        "mcp-infra__simulation__monte_carlo_run",
        "mcp-infra__calibration__bayesian_state",
        "mcp-infra__control_room__agent_memory_read",
        "mcp-infra__control_room__agent_memory_write",
    ):
        assert tool in allowed_tools
    # Margin and cost move with FX and rates, so Banxico/INEGI context applies.
    assert "mcp-infra__market_context_read" in allowed_tools
    # No other domain's aggregate read leaks in.
    assert "mcp-infra__control_room__operations_kpis_read" not in allowed_tools
    assert "mcp-infra__control_room__risk_kpis_read" not in allowed_tools
    assert len(allowed_tools) == len(set(allowed_tools))


def test_monitor_block_sets_the_two_fields_whose_absence_is_silent() -> None:
    _, _, extra = _contract()
    monitor = extra["monitor"]
    assert monitor["wisdom_bit_id"] == "WB-FINANZAS"
    assert monitor["wisdom_bit_id"] != "WB-TALENTO"
    assert monitor["domain"] == "Finanzas"
    assert monitor["domain"] != "Recursos Humanos"
    assert monitor["engine"] == "wisdom_bit"
    assert monitor["dedup_key"] == "sap_s4hana:WB-FINANZAS:workspace"
    assert monitor["recommendation_only"] is True
    assert monitor["writeback_enabled"] is False
    assert monitor["severity"] == "medium"
    # source_dataset for the alert names the domain view, not one Gold relation.
    assert monitor["dataset"] == "finance_kpis"


def test_every_engine_is_disabled_and_says_why() -> None:
    _, _, extra = _contract()
    engines = {engine["name"]: engine for engine in extra["monitor"]["engines"]}
    assert set(engines) == {
        "monte_carlo",
        "bayesian_calibration",
        "decision_orchestrator",
    }
    for name, engine in engines.items():
        assert engine["enabled"] is False, name
        assert engine.get("reason"), f"{name} must state why it is disabled"
    assert engines["monte_carlo"]["reason"] == MONTE_CARLO_DISABLED_REASON
    assert (
        engines["decision_orchestrator"]["reason"]
        == DECISION_ORCHESTRATOR_DISABLED_REASON
    )
    # Carried while disabled so enabling it later cannot trip the runtime guard
    # "monte_carlo requires explicit seed".
    assert isinstance(engines["monte_carlo"]["seed"], int)
    assert engines["bayesian_calibration"]["calibration_group"] == (
        "sap_s4hana:finance_margin"
    )


def test_schedule_is_enabled_and_does_not_collide_with_the_other_monitors() -> None:
    _, _, extra = _contract()
    schedule = extra["schedule"]
    # A scheduled SELECT hides the row unless extra.schedule.enabled reads true,
    # and find_due_agents only considers rows whose extra has a schedule key.
    assert schedule["enabled"] is True
    assert schedule["tz"] == "UTC"
    assert schedule["cron"] == "2,17,32,47 * * * *"
    minutes = {int(part) for part in schedule["cron"].split(" ")[0].split(",")}
    assert minutes.isdisjoint({7, 22, 37, 52})  # operations
    assert minutes.isdisjoint({12, 27, 42, 57})  # risk


def test_prompt_is_spanish_and_states_what_it_must_not_promise() -> None:
    instructions = FINANCE_MONITOR_SPEC.instructions
    assert instructions.startswith("Eres el monitor programado de Finanzas")
    for section in ("## Objetivo", "## Que vigilas", "## Cuando alertas"):
        assert section in instructions
    # The honesty clauses Mission 1 established for these exact aggregates.
    assert "moneda verificada" in instructions
    assert "proxy" in instructions
    assert "recommendation_only" in instructions
    # Never claims to write to the source system.
    assert "No escribes en SAP" in instructions
    # Tells the monitor to consult shared memory before reporting a limitation.
    assert "memoria compartida" in instructions


def test_personality_is_reused_verbatim_from_the_conversational_seed() -> None:
    # infra/init/86_sap_s4hana_agents_seed.sql, Controller Financiero.
    assert FINANCE_MONITOR_SPEC.personality == (
        "Riguroso y analitico, tono Controller. Cifras y riesgo arriba, "
        "evidencia abajo. Honesto sobre datos parciales. Idioma del usuario."
    )


def test_model_is_the_system_default_and_not_an_upgrade() -> None:
    # 17_agents.sql: model TEXT NOT NULL DEFAULT 'claude-sonnet-4-6'.
    assert FINANCE_MONITOR_SPEC.model == "claude-sonnet-4-6"
    assert "opus" not in FINANCE_MONITOR_SPEC.model
    assert FINANCE_MONITOR_SPEC.max_tokens == 2400
    assert FINANCE_MONITOR_SPEC.temperature == 0.2


def test_memory_subjects_name_the_real_mission_one_gaps() -> None:
    assert "cost_center_budget" in FINANCE_MEMORY_SUBJECTS
    assert "pnl_mensual.base_currency" in FINANCE_MEMORY_SUBJECTS


def test_row_identity_and_repair_detection() -> None:
    row = {"cartridge_id": "sap_s4hana", "slug": FINANCE_MONITOR_SLUG}
    assert is_finance_monitor_row(row) is True
    assert is_finance_monitor_row({**row, "slug": "other"}) is False
    assert is_finance_monitor_row(None) is False
    # Another domain's monitor row is not ours to repair.
    assert finance_monitor_needs_runtime_repair(
        {"cartridge_id": "salesforce", "slug": "salesforce_ops_liaison_monitor"}
    ) is False
    # Our row with no contract, the wrong role, or no cron needs repair.
    assert finance_monitor_needs_runtime_repair(row) is True
    _, _, extra = _contract()
    assert finance_monitor_needs_runtime_repair({**row, "extra": extra}) is False
    assert finance_monitor_needs_runtime_repair(
        {**row, "extra": {**extra, "role": "assistant"}}
    ) is True
    assert finance_monitor_needs_runtime_repair(
        {**row, "extra": {**extra, "monitor": {}}}
    ) is True
    assert finance_monitor_needs_runtime_repair(
        {**row, "extra": {**extra, "schedule": {"enabled": True, "cron": ""}}}
    ) is True


def _payload(status: str, signal_count: int) -> dict:
    items = [{"metric": f"m{index}"} for index in range(signal_count)]
    return {
        "status": status,
        "tenant_id": "t",
        "workspace_id": "w",
        "signals": {"count": len(items), "items": items},
        "blockers": [],
        "evidence": {
            "engine_results": [
                {"engine": "monte_carlo", "status": "skipped", "reason": "disabled"},
                {
                    "engine": "bayesian_calibration",
                    "status": "skipped",
                    "reason": "disabled",
                },
                {
                    "engine": "decision_orchestrator",
                    "status": "skipped",
                    "reason": "disabled",
                },
            ]
        },
    }


def test_disabled_engines_do_not_silence_the_alert() -> None:
    _, _, extra = _contract()
    contract = extra["monitor"]
    # This is the whole reason the engines ship disabled rather than enabled:
    # "skipped" is not in _engine_is_incomplete's {blocked, error}.
    assert monitor_should_alert(contract, _payload("degraded", 2)) is True


def test_a_ready_domain_stays_quiet_and_an_unavailable_one_fails_closed() -> None:
    _, _, extra = _contract()
    contract = extra["monitor"]
    # Ready with nothing to say emits no signals, so no alert. The threshold's
    # min_signal_count is an OR-ed early return, so this is the only real gate.
    assert monitor_should_alert(contract, _payload("ready", 0)) is False
    # No evidence, no alert, even with signals attached.
    assert monitor_should_alert(contract, _payload("unavailable", 2)) is False


def test_a_blocked_engine_would_silence_the_alert() -> None:
    _, _, extra = _contract()
    payload = _payload("degraded", 2)
    payload["evidence"]["engine_results"][0] = {
        "engine": "monte_carlo",
        "status": "blocked",
        "reason": "missing_simulation_inputs",
    }
    assert monitor_should_alert(extra["monitor"], payload) is False


# ── the wisdom bit this monitor runs ─────────────────────────────────────────

METRIC_NAMES = ("billable_hours_logged", "labor_cost_by_department", "project_margin")


def _view(status: str, metrics: dict) -> dict:
    return {
        "domain": "finance",
        "generated_at": "2026-09-15T00:00:00+00:00",
        "status": status,
        "named_rows": 0,
        "metrics": metrics,
        "evidence_refs": [],
        "notes": [],
        "unavailable_metrics": [
            name for name, item in metrics.items() if item["status"] == "unavailable"
        ],
        "degraded_metrics": [
            name for name, item in metrics.items() if item["status"] == "degraded"
        ],
    }


def _metric(status: str, error: str | None = None, notes: list | None = None) -> dict:
    return {"status": status, "error": error, "notes": notes or []}


def test_wisdom_bit_payload_is_silent_when_every_metric_is_ready() -> None:
    from app.services.control_room import domain_wisdom_bits

    view = _view("ready", {name: _metric("ready") for name in METRIC_NAMES})
    payload = domain_wisdom_bits.build_payload(FINANCE_MONITOR_SPEC, view)
    assert payload["wisdom_bit_id"] == "WB-FINANZAS"
    assert payload["cartridge_id"] == "sap_s4hana"
    assert payload["status"] == "ready"
    assert payload["data_sufficient"] is True
    # No signal means no alert, and that is the only real gate.
    assert payload["signals"] == {"count": 0, "items": []}
    assert payload["blockers"] == []
    _, _, extra = _contract()
    full = {**payload, "tenant_id": "t", "workspace_id": "w"}
    full["evidence"] = {"engine_results": []}
    assert monitor_should_alert(extra["monitor"], full) is False


def test_wisdom_bit_payload_raises_one_signal_per_unhealthy_metric() -> None:
    from app.services.control_room import domain_wisdom_bits

    names = list(METRIC_NAMES)
    view = _view(
        "degraded",
        {
            names[0]: _metric("ready"),
            names[1]: _metric("degraded", notes=["falta una cifra oficial"]),
            names[2]: _metric("unavailable", error="no hay datos publicados"),
        },
    )
    payload = domain_wisdom_bits.build_payload(FINANCE_MONITOR_SPEC, view)
    assert payload["signals"]["count"] == 2
    # _signal_count refuses the whole payload unless count == len(items) exactly.
    assert payload["signals"]["count"] == len(payload["signals"]["items"])
    assert all(isinstance(item, dict) and item for item in payload["signals"]["items"])
    # Business labels travel, never the metric key.
    assert all(item["metric"] != names[1] for item in payload["signals"]["items"])
    assert payload["blockers"] and "sin evidencia disponible" in payload["blockers"][0]
    assert payload["coverage"]["ready"] and payload["coverage"]["unavailable"]
    # And this payload does alert.
    _, _, extra = _contract()
    full = {**payload, "tenant_id": "t", "workspace_id": "w"}
    full["evidence"] = {"engine_results": []}
    assert monitor_should_alert(extra["monitor"], full) is True


def test_wisdom_bit_payload_fails_closed_when_the_domain_is_unavailable() -> None:
    from app.services.control_room import domain_wisdom_bits

    view = _view("unavailable", {name: _metric("unavailable") for name in METRIC_NAMES})
    payload = domain_wisdom_bits.build_payload(FINANCE_MONITOR_SPEC, view)
    assert payload["status"] == "unavailable"
    # Explicit rather than implied: no evidence, no alert.
    assert payload["data_sufficient"] is False
    assert payload["decision_mode"] == "recommendation_only"
    assert payload["writeback_enabled"] is False
    _, _, extra = _contract()
    full = {**payload, "tenant_id": "t", "workspace_id": "w"}
    full["evidence"] = {"engine_results": []}
    assert monitor_should_alert(extra["monitor"], full) is False

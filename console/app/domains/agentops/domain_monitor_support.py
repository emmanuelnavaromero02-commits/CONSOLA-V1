"""Shared builder for the Finance / Operations / Risk AgentOps monitor contracts.

Mission 4 brings three domains up to the Talent pattern
(``successfactors_talent_monitor``). Talent hardcodes every literal inline
because it was the only monitor; three more would mean three near-identical
200-line files whose only real content is a handful of domain facts. So the
*shape* of the contract lives here once, and each domain module declares only
what is true about that domain.

What a monitor contract actually is, in this repository: not a typed object and
not a table. It is the ``extra`` jsonb column on ``agents``, and the parts the
runtime reads are ``extra.role == "monitor"``, ``extra.schedule.cron`` and the
``extra.monitor`` dict. There is no ``monitor_contract`` column. A contract
builder therefore returns the same 3-tuple Talent's does —
``(allowed_tools, rag_filter, extra)`` — so the seed SQL, the runtime repair and
the tests all read one source of truth.

Three runtime facts shaped the defaults below, all of them verified in
``agent_runtime`` rather than assumed:

* ``wisdom_bit_id`` is NOT optional in practice. ``run_scheduled_monitor`` falls
  back to the literal ``"WB-TALENTO"`` when a contract omits it, so a Finance
  monitor without its own id would silently run the Talent wisdom bit.
* ``domain`` is NOT optional either. The alert argument builder falls back to
  ``"Recursos Humanos"``, so a Finance alert with no ``domain`` would be filed
  under HR in Control Room.
* An engine marked ``enabled: false`` is reported as ``skipped``, and
  ``monitor_alert_policy._engine_is_incomplete`` only treats ``blocked`` and
  ``error`` as incomplete. A disabled engine therefore does NOT suppress
  alerting, while a *blocked* one does. That distinction is why the engines that
  have no data yet are disabled with a stated reason instead of being left on to
  fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MONITOR_ROLE = "monitor"
MONITOR_CATEGORY = "control_room"
MONITOR_SCOPE = "workspace"

# The deterministic chain in run_scheduled_monitor always calls wisdom_bits__run
# first and raise_analysis_alert last, and sync_agentops_monitor_candidates only
# accepts an agent whose allowed_tools intersects SYNC_AGENTOPS_TOOLS. Both are
# therefore structural, not optional extras.
CHAIN_TOOLS: tuple[str, ...] = (
    "mcp-infra__wisdom_bits__run",
    "mcp-infra__control_room__raise_analysis_alert",
)
# AgentOps compute surface granted to every domain monitor.
ANALYSIS_TOOLS: tuple[str, ...] = (
    "mcp-infra__decision__orchestrate",
    "mcp-infra__simulation__monte_carlo_run",
    "mcp-infra__calibration__bayesian_state",
)
# Shared memory between agents (Mission 4, part B).
MEMORY_TOOLS: tuple[str, ...] = (
    "mcp-infra__control_room__agent_memory_read",
    "mcp-infra__control_room__agent_memory_write",
)
# Kept from the conversational agents so a monitor can still describe a schema
# when it has to explain a gap. Both are read-only.
REFINEMENT_TOOLS: tuple[str, ...] = (
    "refinement__query_dataset",
    "refinement__get_schema",
)
MARKET_CONTEXT_TOOL = "mcp-infra__market_context_read"

# Alert threshold shared by the three domains, and the same one Talent uses.
#
# Read monitor_alert_policy.monitor_should_alert carefully before changing this:
# the three threshold keys are OR-ed early returns, not AND-ed gates. A status
# outside status_not_in returns True immediately; so does blockers_present with
# any blocker; and the last clause returns True whenever signal_count > 0 and
# signal_count >= min_signal_count. Because min_signal_count is 1, ANY payload
# carrying at least one signal alerts, whatever its status.
#
# So the real gate is upstream, in how many signals the wisdom bit emits. The
# domain wisdom-bit handlers emit a signal only for a metric that is degraded or
# unavailable, or that carries a limitation worth raising; a fully ready domain
# emits zero signals and therefore never alerts. What the status buys on top of
# that is fail-closed silence: "unavailable" and "partial" are in
# _INSUFFICIENT_STATUSES, so they suppress the alert outright, while "degraded"
# is deliberately not, so a degraded domain can still speak.
DEFAULT_THRESHOLD: dict[str, Any] = {
    "status_not_in": ["ready"],
    "min_signal_count": 1,
    "blockers_present": True,
}

# Why the simulation engines ship disabled. Talent feeds Monte Carlo from
# sap_successfactors_talent_simulation_inputs, a Gold dataset that derives the
# distributions (baseline_value, expected_delta, delay_days, ...) from live
# aggregates. No equivalent dataset exists for these three domains, and putting
# invented distribution parameters in a contract would be fabricating data.
# Recorded in docs/data_gaps.md.
MONTE_CARLO_DISABLED_REASON = (
    "sin dataset de inputs de simulacion para este dominio: no hay "
    "distribuciones agregadas publicadas, ver docs/data_gaps.md"
)
# Bayesian calibration needs recorded outcomes for its group. A brand-new group
# has none, and an engine that BLOCKS would suppress every alert, so it ships
# disabled with the reason stated instead.
BAYESIAN_DISABLED_REASON = (
    "sin historial de calibracion para este grupo todavia: se habilita cuando "
    "existan resultados registrados"
)
# See _decision_engine for the full reasoning: the provenance check only trusts
# WB-TALENTO, so an enabled orchestrator errors and an errored engine suppresses
# the alert.
DECISION_ORCHESTRATOR_DISABLED_REASON = (
    "la procedencia de wisdom bit solo esta habilitada para el monitor de "
    "Talento: se habilita cuando este dominio tenga alertas de wisdom bit ya "
    "publicadas y su id sea reconocido como procedencia durable"
)


@dataclass(frozen=True)
class DomainMonitorSpec:
    """Everything that is true about one domain's monitor.

    ``kpi_tool`` is the bare Mission 2 tool name (for example
    ``control_room__finance_kpis_read``); the server prefix is added here so a
    domain module never has to know about tool namespacing.

    ``source_label`` is what lands in the alert's ``source_dataset`` field. It
    names the domain KPI view, not a Gold relation, because each view reads
    several relations and naming one of them would misattribute the evidence.
    """

    key: str
    cartridge_id: str
    slug: str
    name: str
    domain: str
    wisdom_bit_id: str
    kpi_tool: str
    source_label: str
    description: str
    instructions: str
    personality: str
    recommended_action: str
    cron: str
    calibration_group: str
    monte_carlo_seed: int
    decision_title: str
    decision_description: str
    risk_metric: str
    rag_cartridges: tuple[str, ...]
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 2400
    temperature: float = 0.2
    severity: str = "medium"
    # Banxico / INEGI / SEC context only helps a domain whose numbers actually
    # move with FX, rates or macro conditions.
    uses_market_context: bool = False
    memory_subjects: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dedup_key(self) -> str:
        return f"{self.cartridge_id}:{self.wisdom_bit_id}:workspace"

    @property
    def model_version(self) -> str:
        return f"{self.wisdom_bit_id.lower().replace('-', '_')}.monitor.v1"


def allowed_tools_for(spec: DomainMonitorSpec) -> list[str]:
    """The agent's allowed_tools column, in a stable order.

    The domain's own KPI read comes first because it is the reason the monitor
    exists; the rest is the shared AgentOps surface.
    """
    tools: list[str] = [f"mcp-infra__{spec.kpi_tool}"]
    tools.extend(CHAIN_TOOLS)
    tools.extend(ANALYSIS_TOOLS)
    if spec.uses_market_context:
        tools.append(MARKET_CONTEXT_TOOL)
    tools.extend(MEMORY_TOOLS)
    tools.extend(REFINEMENT_TOOLS)
    return tools


def rag_filter_for(spec: DomainMonitorSpec) -> dict[str, Any]:
    return {
        "cartridges": list(spec.rag_cartridges),
        "kinds": ["document", "schema"],
    }


def _monte_carlo_engine(spec: DomainMonitorSpec) -> dict[str, Any]:
    """Monte Carlo spec, disabled until the domain has simulation inputs.

    The seed and the bounds are carried even while disabled so that enabling it
    later cannot trip the runtime's fail-closed guards ("monte_carlo requires
    explicit seed"). ``input_variables`` is deliberately absent: the runtime
    blocks a Monte Carlo spec without a non-empty dict, which is the correct
    outcome while no distributions exist.
    """
    return {
        "name": "monte_carlo",
        "enabled": False,
        "reason": MONTE_CARLO_DISABLED_REASON,
        "source_type": "wisdom_bit",
        "source_id": spec.wisdom_bit_id,
        "horizon_days": 30,
        "iterations": 1000,
        "seed": spec.monte_carlo_seed,
        "model_version": spec.model_version,
        "output_metric": "delta",
        "breach_threshold": -5,
        "breach_direction": "below",
        "assumptions": {
            "basis": f"Agregado {spec.wisdom_bit_id} desde las vistas de dominio.",
            "privacy": "Solo agregados: sin nombres, sin identificadores de persona.",
            "decision_mode": "recommendation_only",
        },
        "evidence_refs": [{"type": "wisdom_bit", "id": spec.wisdom_bit_id}],
    }


def _bayesian_engine(spec: DomainMonitorSpec) -> dict[str, Any]:
    return {
        "name": "bayesian_calibration",
        "enabled": False,
        "reason": BAYESIAN_DISABLED_REASON,
        "calibration_group": spec.calibration_group,
        "model_version": "bayesian_calibration.v1",
        "limit": 10,
        "assumptions": {
            "basis": f"Estado historico agregado de {spec.domain}.",
            "privacy": "Solo agregados: sin nombres, sin identificadores de persona.",
            "decision_mode": "recommendation_only",
        },
        "evidence_refs": [
            {"type": "calibration_group", "id": spec.calibration_group}
        ],
    }


def _decision_engine(spec: DomainMonitorSpec) -> dict[str, Any]:
    """Decision orchestrator spec, disabled for the same reason it would fail.

    Leaving this enabled looks harmless and is not: it would silence the monitor
    completely. ``decision__orchestrate`` with ``source_type="wisdom_bit"`` calls
    ``wisdom_source.durable_wisdom_exists``, whose first line returns False for
    any source_id other than ``WB-TALENTO``, so the call raises 409
    ``source_provenance_untrusted``. That lands as an engine result with status
    ``error``, and ``monitor_alert_policy._engine_is_incomplete`` treats any
    errored engine as insufficient evidence, so ``monitor_should_alert`` returns
    False and the advisory alert the monitor exists to raise never happens.

    Teaching ``durable_wisdom_exists`` the three new ids would not be enough
    either: it also requires a pre-existing Control Room ``agent_alert`` row of
    wisdom-bit origin, which only exists after a first successful alert — a
    deadlock on a new domain. That provenance rule is a deliberate trust
    boundary (a decision may only be orchestrated from evidence that was already
    durably published), so it is respected rather than loosened.

    ``execute_engines`` stays False and ``engine_inputs`` empty for when this is
    enabled: the only engine it could chain is Monte Carlo, which has no inputs
    for this domain either.
    """
    return {
        "name": "decision_orchestrator",
        "enabled": False,
        "reason": DECISION_ORCHESTRATOR_DISABLED_REASON,
        "source_type": "wisdom_bit",
        "source_id": spec.wisdom_bit_id,
        "title": spec.decision_title,
        "description": spec.decision_description,
        "time_horizon": "30d",
        "metrics": {
            "risk_metric": spec.risk_metric,
            "target": "recommendation_only",
            "privacy": "aggregated",
        },
        "constraints": {
            "recommendation_only": True,
            "no_external_writeback": True,
            "no_pii": True,
        },
        "evidence_refs": [{"type": "wisdom_bit", "id": spec.wisdom_bit_id}],
        "execute_engines": False,
        "engine_inputs": {},
    }


def monitor_block_for(spec: DomainMonitorSpec) -> dict[str, Any]:
    """The ``extra.monitor`` contract itself."""
    return {
        "engine": "wisdom_bit",
        "wisdom_bit_id": spec.wisdom_bit_id,
        # Without this the alert builder files the finding under
        # "Recursos Humanos".
        "domain": spec.domain,
        "dataset": spec.source_label,
        "threshold": dict(DEFAULT_THRESHOLD),
        "severity": spec.severity,
        "dedup_key": spec.dedup_key,
        "recommended_action": spec.recommended_action,
        "recommendation_only": True,
        "writeback_enabled": False,
        "engines": [
            _monte_carlo_engine(spec),
            _bayesian_engine(spec),
            _decision_engine(spec),
        ],
    }


def schedule_for(spec: DomainMonitorSpec) -> dict[str, Any]:
    return {
        "enabled": True,
        "cron": spec.cron,
        "tz": "UTC",
        "prompt": (
            f"Ejecuta el monitor {spec.wisdom_bit_id}: lee los KPI agregados del "
            f"dominio, revisa la memoria compartida antes de reportar, evalua "
            f"blockers y senales, y publica una alerta advisory solo con "
            f"evidencia agregada y recommendation_only."
        ),
    }


def extra_for(spec: DomainMonitorSpec) -> dict[str, Any]:
    return {
        "role": MONITOR_ROLE,
        "category": MONITOR_CATEGORY,
        "scope": MONITOR_SCOPE,
        "variables": {},
        "schedule": schedule_for(spec),
        "monitor": monitor_block_for(spec),
        "memory_subjects": list(spec.memory_subjects),
    }


def build_contract(
    spec: DomainMonitorSpec,
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Same 3-tuple shape as ``successfactors_talent_monitor_contract()``."""
    return allowed_tools_for(spec), rag_filter_for(spec), extra_for(spec)


def is_domain_monitor_row(agent: Any, spec: DomainMonitorSpec) -> bool:
    if not isinstance(agent, dict):
        return False
    return (
        str(agent.get("cartridge_id") or "").strip() == spec.cartridge_id
        and str(agent.get("slug") or "").strip() == spec.slug
    )


def has_monitor_contract(agent: Any) -> bool:
    extra = agent.get("extra") if isinstance(agent, dict) else None
    monitor = extra.get("monitor") if isinstance(extra, dict) else None
    return isinstance(monitor, dict) and bool(monitor)


def needs_runtime_repair(agent: Any, spec: DomainMonitorSpec) -> bool:
    """True when the row exists but would be rejected by the scheduled path.

    Mirrors ``successfactors_talent_monitor_needs_runtime_repair``: the cron
    entry point refuses an agent whose ``extra.role`` is not ``monitor`` or whose
    ``extra.monitor`` is missing, and it does so before any reservation, so the
    run simply never happens.
    """
    if not is_domain_monitor_row(agent, spec):
        return False
    extra = agent.get("extra") if isinstance(agent, dict) else None
    if not isinstance(extra, dict):
        return True
    role = str(extra.get("role") or "").strip().lower()
    if role != MONITOR_ROLE:
        return True
    if not has_monitor_contract(agent):
        return True
    schedule = extra.get("schedule")
    return not (isinstance(schedule, dict) and str(schedule.get("cron") or "").strip())


__all__ = (
    "ANALYSIS_TOOLS",
    "BAYESIAN_DISABLED_REASON",
    "CHAIN_TOOLS",
    "DECISION_ORCHESTRATOR_DISABLED_REASON",
    "DEFAULT_THRESHOLD",
    "DomainMonitorSpec",
    "MARKET_CONTEXT_TOOL",
    "MEMORY_TOOLS",
    "MONITOR_CATEGORY",
    "MONITOR_ROLE",
    "MONITOR_SCOPE",
    "MONTE_CARLO_DISABLED_REASON",
    "REFINEMENT_TOOLS",
    "allowed_tools_for",
    "build_contract",
    "extra_for",
    "has_monitor_contract",
    "is_domain_monitor_row",
    "monitor_block_for",
    "needs_runtime_repair",
    "rag_filter_for",
    "schedule_for",
)

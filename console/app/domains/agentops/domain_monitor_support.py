from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

MONITOR_ROLE = "monitor"
MONITOR_CATEGORY = "control_room"
MONITOR_SCOPE = "workspace"

CHAIN_TOOLS: tuple[str, ...] = (
    "mcp-infra__wisdom_bits__run",
    "mcp-infra__control_room__raise_analysis_alert",
)
ANALYSIS_TOOLS: tuple[str, ...] = (
    "mcp-infra__decision__orchestrate",
    "mcp-infra__simulation__monte_carlo_run",
    "mcp-infra__calibration__bayesian_state",
)
MEMORY_TOOLS: tuple[str, ...] = (
    "mcp-infra__control_room__agent_memory_read",
    "mcp-infra__control_room__agent_memory_write",
)
REFINEMENT_TOOLS: tuple[str, ...] = (
    "refinement__query_dataset",
    "refinement__get_schema",
)
MARKET_CONTEXT_TOOL = "mcp-infra__market_context_read"

DEFAULT_THRESHOLD: dict[str, Any] = {
    "status_not_in": ["ready"],
    "min_signal_count": 1,
    "blockers_present": True,
}

MONTE_CARLO_DISABLED_REASON = (
    "sin dataset de inputs de simulacion para este dominio: no hay "
    "distribuciones agregadas publicadas, ver docs/data_gaps.md"
)
BAYESIAN_DISABLED_REASON = (
    "sin historial de calibracion para este grupo todavia: se habilita cuando "
    "existan resultados registrados"
)
DECISION_ORCHESTRATOR_DISABLED_REASON = (
    "la procedencia de wisdom bit solo esta habilitada para el monitor de "
    "Talento: se habilita cuando este dominio tenga alertas de wisdom bit ya "
    "publicadas y su id sea reconocido como procedencia durable"
)


@dataclass(frozen=True)
class DomainMonitorSpec:

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
    uses_market_context: bool = False
    memory_subjects: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dedup_key(self) -> str:
        return f"{self.cartridge_id}:{self.wisdom_bit_id}:workspace"

    @property
    def model_version(self) -> str:
        return f"{self.wisdom_bit_id.lower().replace('-', '_')}.monitor.v1"


def allowed_tools_for(spec: DomainMonitorSpec) -> list[str]:
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
    return {
        "engine": "wisdom_bit",
        "wisdom_bit_id": spec.wisdom_bit_id,
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


_UPDATE_SQL = """
    UPDATE agents
       SET name = $4,
           description = $5,
           instructions = $6,
           personality = $7,
           allowed_tools = $8::jsonb,
           rag_filter = $9::jsonb,
           model = $10,
           max_tokens = $11,
           temperature = $12,
           extra = $13::jsonb,
           is_active = TRUE,
           updated_at = NOW()
     WHERE tenant_id = $1::uuid
       AND workspace_id = $2::uuid
       AND cartridge_id = $14
       AND slug = $3
"""

_INSERT_SQL = """
    INSERT INTO agents (
        tenant_id, workspace_id, cartridge_id, slug, name, description,
        instructions, personality, allowed_tools, rag_filter, model,
        max_tokens, temperature, extra, is_active
    )
    SELECT
        $1::uuid, $2::uuid, $14, $3, $4, $5,
        $6, $7, $8::jsonb, $9::jsonb, $10,
        $11, $12, $13::jsonb, TRUE
    WHERE NOT EXISTS (
        SELECT 1
          FROM agents
         WHERE workspace_id = $2::uuid
           AND cartridge_id = $14
           AND slug = $3
    )
"""


async def ensure_domain_monitor(
    user: dict | None,
    spec: DomainMonitorSpec,
    *,
    get_db_pool: Any,
    build_security_context: Any,
    logger: Any,
) -> None:
    ctx = build_security_context(user)
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        return

    allowed_tools, rag_filter, extra = build_contract(spec)
    args = (
        tenant_id,
        workspace_id,
        spec.slug,
        spec.name,
        spec.description,
        spec.instructions,
        spec.personality,
        json.dumps(allowed_tools, ensure_ascii=False),
        json.dumps(rag_filter, ensure_ascii=False),
        spec.model,
        spec.max_tokens,
        spec.temperature,
        json.dumps(extra, ensure_ascii=False, sort_keys=True),
        spec.cartridge_id,
    )
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), "
                    "set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
                updated = await conn.execute(_UPDATE_SQL, *args)
                if str(updated).endswith(" 0"):
                    await conn.execute(_INSERT_SQL, *args)
    except Exception:
        logger.warning(
            "Could not ensure the %s AgentOps monitor for workspace=%s",
            spec.key,
            workspace_id,
            exc_info=True,
        )


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
    "ensure_domain_monitor",
    "extra_for",
    "has_monitor_contract",
    "is_domain_monitor_row",
    "monitor_block_for",
    "needs_runtime_repair",
    "rag_filter_for",
    "schedule_for",
)

"""SuccessFactors Talent AgentOps monitor contract helpers."""

from __future__ import annotations

import json
from typing import Any


SUCCESSFACTORS_TALENT_MONITOR_SLUG = "sap_successfactors_talent_monitor"
SYNC_AGENTOPS_TOOLS = {
    "mcp-infra__simulation__monte_carlo_run",
    "mcp-infra__market_context_read",
    "mcp-infra__decision__orchestrate",
    "mcp-infra__wisdom_bits__run",
    "mcp-infra__control_room__raise_alert",
    "mcp-infra__control_room__raise_analysis_alert",
    "infra__simulation__monte_carlo_run",
    "infra__market_context_read",
    "infra__decision__orchestrate",
    "infra__wisdom_bits__run",
    "infra__control_room__raise_alert",
    "infra__control_room__raise_analysis_alert",
}


def sync_agentops_is_terminal(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("status") or "").lower() in {
        "success",
        "partial",
        "failed",
        "skipped",
    }


def sync_agentops_monitor_candidates(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for agent in agents:
        if not agent.get("is_active", True):
            continue
        if not (
            str(agent.get("tenant_id") or "").strip()
            and str(agent.get("workspace_id") or "").strip()
        ):
            continue
        extra = agent.get("extra") if isinstance(agent.get("extra"), dict) else {}
        role = str((extra or {}).get("role") or "").strip().lower()
        monitor = (extra or {}).get("monitor")
        allowed_tools = agent.get("allowed_tools") or []
        allowed_set = {
            str(tool)
            for tool in allowed_tools
            if isinstance(tool, str) and tool.strip()
        }
        if (
            role == "monitor"
            and isinstance(monitor, dict)
            and monitor
            and (allowed_set & SYNC_AGENTOPS_TOOLS)
        ):
            candidates.append(agent)
    return candidates


def successfactors_talent_monitor_contract() -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    allowed_tools = [
        "mcp-infra__wisdom_bits__run",
        "mcp-infra__control_room__raise_analysis_alert",
        "mcp-infra__decision__orchestrate",
        "mcp-infra__simulation__monte_carlo_run",
        "mcp-infra__market_context_read",
        "mcp-infra__calibration__bayesian_state",
        "refinement__query_dataset",
        "refinement__get_schema",
    ]
    rag_filter = {"cartridges": ["sap_successfactors"], "kinds": ["document", "schema"]}
    extra = {
        "role": "monitor",
        "category": "control_room",
        "scope": "workspace",
        "variables": {},
        "schedule": {
            "enabled": True,
            "cron": "*/15 * * * *",
            "tz": "UTC",
            "prompt": "Ejecuta el monitor WB-TALENTO con evidencia agregada y recommendation_only.",
        },
        "monitor": {
            "engine": "wisdom_bit",
            "wisdom_bit_id": "WB-TALENTO",
            "dataset": "sap_successfactors_talent_operational_features",
            "threshold": {
                "status_not_in": ["ready"],
                "min_signal_count": 1,
                "blockers_present": True,
            },
            "severity": "medium",
            "dedup_key": "sap_successfactors:WB-TALENTO:workspace",
            "recommended_action": "Revisar blockers C/P/A y priorizar acciones supervisadas en Control Room.",
            "recommendation_only": True,
            "writeback_enabled": False,
            "engines": [
                {
                    "name": "monte_carlo",
                    "enabled": True,
                    "source_type": "wisdom_bit",
                    "source_id": "WB-TALENTO",
                    "horizon_days": 30,
                    "iterations": 1000,
                    "seed": 45120,
                    "model_version": "wb-talento.monitor.v2",
                    "output_metric": "delta",
                    "breach_threshold": -5,
                    "breach_direction": "below",
                    "input_dataset": "sap_successfactors_talent_simulation_inputs",
                    "input_variables_field": "input_variables_json",
                    "assumptions_field": "assumptions_json",
                    "evidence_refs_field": "evidence_refs_json",
                    "status_field": "input_status",
                    "ready_statuses": ["ready"],
                    "assumptions": {
                        "basis": "Agregado WB-TALENTO desde Gold operativo.",
                        "privacy": "Sin full_name, user_id, PERNR, salario ni payCompValue.",
                        "decision_mode": "recommendation_only",
                    },
                    "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
                },
                {
                    "name": "bayesian_calibration",
                    "enabled": True,
                    "calibration_group": "sap_successfactors:talent_readiness",
                    "limit": 10,
                    "assumptions": {
                        "basis": "Estado historico agregado de readiness de talento.",
                        "privacy": "Sin full_name, user_id, PERNR, salario ni payCompValue.",
                        "decision_mode": "recommendation_only",
                    },
                    "evidence_refs": [
                        {
                            "type": "calibration_group",
                            "id": "sap_successfactors:talent_readiness",
                        }
                    ],
                },
                {
                    "name": "decision_orchestrator",
                    "enabled": True,
                    "source_type": "wisdom_bit",
                    "source_id": "WB-TALENTO",
                    "title": "Decision operativa WB-TALENTO",
                    "description": "Evaluar senales agregadas de talento con seguimiento supervisado.",
                    "time_horizon": "30d",
                    "metrics": {
                        "risk_metric": "talent_readiness_delta",
                        "target": "recommendation_only",
                        "privacy": "aggregated",
                    },
                    "constraints": {
                        "recommendation_only": True,
                        "no_external_writeback": True,
                        "no_pii": True,
                    },
                    "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
                    "execute_engines": True,
                    "engine_inputs": {
                        "monte_carlo": {
                            "source_type": "wisdom_bit",
                            "source_id": "WB-TALENTO",
                            "horizon_days": 30,
                            "iterations": 1000,
                            "seed": 45120,
                            "model_version": "wb-talento.monitor.v2",
                            "input_dataset": "sap_successfactors_talent_simulation_inputs",
                            "input_variables_field": "input_variables_json",
                            "assumptions_field": "assumptions_json",
                            "evidence_refs_field": "evidence_refs_json",
                            "status_field": "input_status",
                            "ready_statuses": ["ready"],
                            "output_metric": "delta",
                            "breach_threshold": -5,
                            "breach_direction": "below",
                            "assumptions": {
                                "basis": "Agregado WB-TALENTO.",
                                "privacy": "Sin PII.",
                                "decision_mode": "recommendation_only",
                            },
                            "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
                        },
                        "bayesian_calibration": {
                            "calibration_group": "sap_successfactors:talent_readiness",
                            "limit": 10,
                        },
                    },
                },
            ],
        },
    }
    return allowed_tools, rag_filter, extra


def is_successfactors_talent_monitor_row(agent: Any) -> bool:
    if not isinstance(agent, dict):
        return False
    return (
        str(agent.get("cartridge_id") or "").strip() == "sap_successfactors"
        and str(agent.get("slug") or "").strip() == SUCCESSFACTORS_TALENT_MONITOR_SLUG
    )


def has_operational_monitor_contract(agent: Any) -> bool:
    extra = agent.get("extra") if isinstance(agent, dict) else None
    monitor = extra.get("monitor") if isinstance(extra, dict) else None
    return isinstance(monitor, dict) and bool(monitor)


def successfactors_talent_monitor_needs_runtime_repair(agent: Any) -> bool:
    if not is_successfactors_talent_monitor_row(agent):
        return False
    extra = agent.get("extra") if isinstance(agent, dict) else None
    if not isinstance(extra, dict):
        return True
    role = str(extra.get("role") or "").strip().lower()
    return role != "monitor" or not has_operational_monitor_contract(agent)


def merge_agent_tools(primary: list[str], secondary: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    candidates: list[Any] = list(primary)
    if isinstance(secondary, list):
        candidates.extend(secondary)
    for tool in candidates:
        if not isinstance(tool, str):
            continue
        value = tool.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def coerce_successfactors_talent_monitor_payload(body: dict) -> dict:
    if not isinstance(body, dict):
        return body
    cartridge_id = str(body.get("cartridge_id") or "").strip()
    slug = str(body.get("slug") or "").strip()
    if cartridge_id != "sap_successfactors" or slug != SUCCESSFACTORS_TALENT_MONITOR_SLUG:
        return body

    allowed_tools, rag_filter, contract_extra = successfactors_talent_monitor_contract()
    patched = dict(body)
    incoming_extra = patched.get("extra") if isinstance(patched.get("extra"), dict) else {}
    merged_extra = {**contract_extra, **incoming_extra}
    merged_extra["role"] = "monitor"
    merged_extra["category"] = str(merged_extra.get("category") or "control_room")
    merged_extra["scope"] = str(merged_extra.get("scope") or "workspace")

    schedule = merged_extra.get("schedule")
    if not isinstance(schedule, dict) or not schedule.get("cron"):
        merged_extra["schedule"] = contract_extra.get("schedule")

    monitor = merged_extra.get("monitor")
    if isinstance(monitor, dict) and monitor:
        merged_monitor = {**contract_extra.get("monitor", {}), **monitor}
        if not isinstance(merged_monitor.get("engines"), list) or not merged_monitor.get(
            "engines"
        ):
            merged_monitor["engines"] = contract_extra.get("monitor", {}).get("engines", [])
        merged_extra["monitor"] = merged_monitor
    else:
        merged_extra["monitor"] = contract_extra.get("monitor")

    if not isinstance(merged_extra.get("variables"), dict):
        merged_extra["variables"] = {}

    patched["extra"] = merged_extra
    patched["role"] = "monitor"
    patched["allowed_tools"] = merge_agent_tools(allowed_tools, patched.get("allowed_tools"))
    if not isinstance(patched.get("rag_filter"), dict) or not patched.get("rag_filter"):
        patched["rag_filter"] = rag_filter
    patched["model"] = str(patched.get("model") or "claude-sonnet-4-6")
    if patched.get("max_tokens") in (None, ""):
        patched["max_tokens"] = 2400
    if patched.get("temperature") in (None, ""):
        patched["temperature"] = 0.2
    return patched


async def ensure_successfactors_talent_monitor(
    user: dict | None,
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

    allowed_tools, rag_filter, extra = successfactors_talent_monitor_contract()
    description = (
        "Monitor programado de WB-TALENTO con evidencia agregada y recommendation_only."
    )
    instructions = (
        "Eres el monitor operativo de SuccessFactors Talent. Usa solo evidencia "
        "agregada, no expongas PII, no escribas en SuccessFactors y mantén todas "
        "las salidas en recommendation_only."
    )
    personality = "Operativo, sobrio y auditable. Idioma del usuario."

    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), "
                    "set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
                updated = await conn.execute(
                    """
                    UPDATE agents
                       SET description = $4,
                           instructions = $5,
                           personality = $6,
                           allowed_tools = $7::jsonb,
                           rag_filter = $8::jsonb,
                           model = 'claude-sonnet-4-6',
                           max_tokens = 2400,
                           temperature = 0.2,
                           extra = $9::jsonb,
                           is_active = TRUE,
                           updated_at = NOW()
                     WHERE tenant_id = $1::uuid
                       AND workspace_id = $2::uuid
                       AND cartridge_id = 'sap_successfactors'
                       AND slug = $3
                    """,
                    tenant_id,
                    workspace_id,
                    SUCCESSFACTORS_TALENT_MONITOR_SLUG,
                    description,
                    instructions,
                    personality,
                    json.dumps(allowed_tools, ensure_ascii=False),
                    json.dumps(rag_filter, ensure_ascii=False),
                    json.dumps(extra, ensure_ascii=False, sort_keys=True),
                )
                if str(updated).endswith(" 0"):
                    await conn.execute(
                        """
                        INSERT INTO agents (
                            tenant_id, workspace_id, cartridge_id, slug, name, description,
                            instructions, personality, allowed_tools, rag_filter, model,
                            max_tokens, temperature, extra, is_active
                        )
                        SELECT
                            $1::uuid, $2::uuid, 'sap_successfactors',
                            'sap_successfactors_talent_monitor',
                            'Talent AgentOps Monitor',
                            $3, $4, $5, $6::jsonb, $7::jsonb,
                            'claude-sonnet-4-6', 2400, 0.2, $8::jsonb, TRUE
                        WHERE NOT EXISTS (
                            SELECT 1
                              FROM agents
                             WHERE workspace_id = $2::uuid
                               AND cartridge_id = 'sap_successfactors'
                               AND slug = 'sap_successfactors_talent_monitor'
                        )
                        """,
                        tenant_id,
                        workspace_id,
                        description,
                        instructions,
                        personality,
                        json.dumps(allowed_tools, ensure_ascii=False),
                        json.dumps(rag_filter, ensure_ascii=False),
                        json.dumps(extra, ensure_ascii=False, sort_keys=True),
                    )
    except Exception:
        logger.warning(
            "Could not ensure SuccessFactors Talent AgentOps monitor for workspace=%s",
            workspace_id,
            exc_info=True,
        )

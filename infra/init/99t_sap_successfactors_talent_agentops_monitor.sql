-- v1.45.114 AgentOps: workspace-scoped SuccessFactors Talent monitor.
--
-- This is intentionally separate from 88_sap_successfactors_agents_seed.sql:
-- the original two agents are conversational global templates. Scheduled
-- monitor agents must be workspace-scoped so agent_runner can mint a signed
-- tenant/workspace security_context before invoking MCP.

WITH active_scope AS (
    SELECT DISTINCT
           ci.tenant_id,
           ci.workspace_id
      FROM cartridge_installations ci
      LEFT JOIN tenant_entitlements te
        ON te.tenant_id = ci.tenant_id
       AND te.workspace_id = ci.workspace_id
       AND te.cartridge_id = ci.cartridge_id
     WHERE ci.cartridge_id = 'sap_successfactors'
       AND ci.status = 'ready'
       AND COALESCE(te.status, 'active') = 'active'
),
fallback_scope AS (
    SELECT t.id AS tenant_id, w.id AS workspace_id
      FROM tenants t
      JOIN workspaces w ON w.tenant_id = t.id
     WHERE t.name = 'Default Tenant'
       AND w.name = 'Main Workspace'
       AND NOT EXISTS (SELECT 1 FROM active_scope)
     ORDER BY w.created_at NULLS LAST, w.id
     LIMIT 1
),
scope AS (
    SELECT tenant_id, workspace_id FROM active_scope
    UNION ALL
    SELECT tenant_id, workspace_id FROM fallback_scope
),
payload AS (
    SELECT
        scope.tenant_id,
        scope.workspace_id,
        'sap_successfactors'::text AS cartridge_id,
        'sap_successfactors_talent_monitor'::text AS slug,
        'Talent AgentOps Monitor'::text AS name,
        'Monitor programado de WB-TALENTO: evalua blockers C/P/A, senales de talento y publica evidencia advisory en Control Room.'::text AS description,
        $$Eres el monitor operativo de SuccessFactors Talent para Control Room.

## Objetivo
Ejecuta una revision programada, segura y auditable de WB-TALENTO. No escribas en SuccessFactors, no apruebes acciones y no expongas PII.

## Flujo obligatorio
1. Ejecuta `mcp-infra__wisdom_bits__run` con `wisdom_bit_id = "WB-TALENTO"` y `cartridge_id = "sap_successfactors"`.
2. Si el WisdomBit devuelve blockers, senales o estado distinto de ready, publica una alerta con `mcp-infra__control_room__raise_analysis_alert`.
3. Usa `engine = "wisdom_bit"`, `analysis_type = "talent_readiness_monitor"`, `source_dataset = "sap_successfactors_talent_signals"` y `cartridge_id = "sap_successfactors"`.
4. Incluye solo evidencia agregada: counts, status, blockers y recomendacion. No incluyas full_name, user_id, PERNR, salario ni payCompValue.
5. Usa `mcp-infra__decision__orchestrate` solo si existe una senal concreta que requiera comparar opciones. Mantener `execute_engines = true` solo con inputs internos seguros.
6. Ejecuta `mcp-infra__simulation__monte_carlo_run` con la configuracion agregada del contrato. No incluyas PII; si falla la simulacion, reporta blocker.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas.$$::text AS instructions,
        'Operativo, sobrio y auditable. Explica breve, cita blockers y deja evidencia estructurada. Idioma del usuario.'::text AS personality,
        '[
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "refinement__query_dataset",
          "refinement__get_schema"
        ]'::jsonb AS allowed_tools,
        '{"cartridges":["sap_successfactors"],"kinds":["document","schema"]}'::jsonb AS rag_filter,
        'claude-sonnet-4-6'::text AS model,
        2400::int AS max_tokens,
        0.2::real AS temperature,
        '{
          "role": "monitor",
          "category": "control_room",
          "scope": "workspace",
          "variables": {},
          "schedule": {
            "enabled": true,
            "cron": "*/15 * * * *",
            "tz": "UTC",
            "prompt": "Ejecuta el monitor WB-TALENTO: corre wisdom_bits__run, evalua blockers/senales y publica control_room__raise_analysis_alert solo con evidencia agregada y recommendation_only."
          },
          "monitor": {
            "engine": "wisdom_bit",
            "wisdom_bit_id": "WB-TALENTO",
            "dataset": "sap_successfactors_talent_signals",
            "threshold": {
              "status_not_in": ["ready"],
              "min_signal_count": 1,
              "blockers_present": true
            },
            "severity": "medium",
            "dedup_key": "sap_successfactors:WB-TALENTO:workspace",
            "recommended_action": "Revisar blockers C/P/A, validar metadata y priorizar acciones supervisadas en Control Room.",
            "recommendation_only": true,
            "writeback_enabled": false,
            "engines": [
              {
                "name": "monte_carlo",
                "enabled": true,
                "source_type": "wisdom_bit",
                "source_id": "WB-TALENTO",
                "horizon_days": 30,
                "iterations": 1000,
                "seed": 45120,
                "model_version": "wb-talento.monitor.v1",
                "output_metric": "delta",
                "breach_threshold": -5,
                "breach_direction": "below",
                "input_variables": {
                  "baseline_value": {"type": "fixed", "value": 100},
                  "expected_delta": {"type": "triangular", "low": -12, "mode": -4, "high": 2},
                  "delay_days": {"type": "triangular", "low": 0, "mode": 5, "high": 14},
                  "cost_per_day": {"type": "fixed", "value": 1},
                  "probability_of_delay": {"type": "triangular", "low": 0.2, "mode": 0.5, "high": 0.8}
                },
                "assumptions": {
                  "basis": "Agregado WB-TALENTO: presion operativa por blockers C/P/A y senales de talento.",
                  "privacy": "Sin full_name, user_id, PERNR, salario ni payCompValue.",
                  "decision_mode": "recommendation_only"
                },
                "evidence_refs": [
                  {"type": "wisdom_bit", "id": "WB-TALENTO"}
                ]
              },
              {
                "name": "decision_orchestrator",
                "enabled": true,
                "source_type": "wisdom_bit",
                "source_id": "WB-TALENTO",
                "title": "Decision operativa WB-TALENTO",
                "description": "Evaluar si las senales agregadas de talento requieren abrir investigacion, simular impacto y mantener seguimiento supervisado.",
                "time_horizon": "30d",
                "metrics": {
                  "risk_metric": "talent_readiness_delta",
                  "target": "recommendation_only",
                  "privacy": "aggregated"
                },
                "constraints": {
                  "recommendation_only": true,
                  "no_external_writeback": true,
                  "no_pii": true
                },
                "evidence_refs": [
                  {"type": "wisdom_bit", "id": "WB-TALENTO"}
                ],
                "execute_engines": true,
                "engine_inputs": {
                  "monte_carlo": {
                    "source_type": "wisdom_bit",
                    "source_id": "WB-TALENTO",
                    "horizon_days": 30,
                    "iterations": 1000,
                    "seed": 45120,
                    "model_version": "wb-talento.monitor.v1",
                    "input_variables": {
                      "baseline_value": {"type": "fixed", "value": 100},
                      "expected_delta": {"type": "triangular", "low": -12, "mode": -4, "high": 2},
                      "delay_days": {"type": "triangular", "low": 0, "mode": 5, "high": 14},
                      "cost_per_day": {"type": "fixed", "value": 1},
                      "probability_of_delay": {"type": "triangular", "low": 0.2, "mode": 0.5, "high": 0.8}
                    },
                    "output_metric": "delta",
                    "breach_threshold": -5,
                    "breach_direction": "below",
                    "assumptions": {
                      "basis": "Agregado WB-TALENTO: blockers C/P/A y senales de talento.",
                      "privacy": "Sin PII.",
                      "decision_mode": "recommendation_only"
                    },
                    "evidence_refs": [
                      {"type": "wisdom_bit", "id": "WB-TALENTO"}
                    ]
                  },
                  "bayesian_calibration": {
                    "calibration_group": "sap_successfactors:talent_readiness"
                  }
                }
              }
            ]
          }
        }'::jsonb AS extra
    FROM scope
),
updated AS (
    UPDATE agents a
       SET name = p.name,
           description = p.description,
           instructions = p.instructions,
           personality = p.personality,
           allowed_tools = p.allowed_tools,
           rag_filter = p.rag_filter,
           model = p.model,
           max_tokens = p.max_tokens,
           temperature = p.temperature,
           extra = p.extra,
           is_active = TRUE,
           updated_at = NOW()
      FROM payload p
     WHERE a.workspace_id = p.workspace_id
       AND a.cartridge_id = p.cartridge_id
       AND a.slug = p.slug
     RETURNING a.id
)
INSERT INTO agents (
    tenant_id, workspace_id, cartridge_id, slug, name, description,
    instructions, personality, allowed_tools, rag_filter, model,
    max_tokens, temperature, extra, is_active
)
SELECT
    tenant_id, workspace_id, cartridge_id, slug, name, description,
    instructions, personality, allowed_tools, rag_filter, model,
    max_tokens, temperature, extra, TRUE
FROM payload
WHERE NOT EXISTS (SELECT 1 FROM updated);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99t_sap_successfactors_talent_agentops_monitor.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

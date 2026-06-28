-- 99zm_sap_successfactors_talent_operational_activation.sql
--
-- Repair already-deployed WB-TALENTO workspaces after the operational feature
-- pack rollout. The tenant-admin decision for this release is explicit: when
-- C/P/A real is not exposed by the tenant, use an approved, versioned internal
-- benchmark as recommendation_only fallback instead of leaving Talent dark.

UPDATE datasets
   SET sources = '["config/sap_successfactors/talent_benchmark_internal"]'::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_benchmark_internal  (gold)  cartridge: sap_successfactors
-- sources: ["config/sap_successfactors/talent_benchmark_internal"]
-- description: Contrato interno versionado para clasificacion Talent. Fallback operativo aprobado y auditable cuando C/P/A real no esta expuesto por el tenant.

SELECT
    'WB-TALENTO' AS source_id,
    'talent_benchmark_internal.v1.approved' AS benchmark_version,
    TRUE AS enabled,
    TRUE AS approved,
    'system:tenant_admin_request' AS approved_by,
    NULL AS approved_at,
    'wb_talento_operational_activation' AS approval_source,
    0.80 AS minimum_profile_coverage,
    80.0 AS readiness_high_threshold,
    60.0 AS readiness_medium_threshold,
    4.0 AS performance_high_threshold,
    3.0 AS performance_medium_threshold,
    4.0 AS potential_high_threshold,
    3.0 AS potential_medium_threshold,
    0.60 AS competency_weight,
    0.25 AS role_coverage_weight,
    0.15 AS tenure_weight,
    '[]' AS blockers,
    'talent_benchmark_internal.v1' AS contract_version,
    CURRENT_TIMESTAMP AS materialized_at
$sql$,
       description = 'Benchmark interno versionado aprobado para WB-TALENTO.',
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_benchmark_internal'
   AND cartridge = 'sap_successfactors';

WITH active_scope AS (
    SELECT DISTINCT ci.tenant_id, ci.workspace_id
      FROM cartridge_installations ci
      LEFT JOIN tenant_entitlements te
        ON te.tenant_id = ci.tenant_id
       AND te.workspace_id = ci.workspace_id
       AND te.cartridge_id = ci.cartridge_id
     WHERE ci.cartridge_id = 'sap_successfactors'
       AND ci.workspace_id IS NOT NULL
       AND ci.status IN ('ready', 'active', 'installed', 'configured')
       AND COALESCE(te.status, 'active') = 'active'
    UNION
    SELECT DISTINCT ec.tenant_id, ec.workspace_id
      FROM entity_config ec
     WHERE ec.cartridge_id = 'sap_successfactors'
       AND ec.workspace_id IS NOT NULL
       AND COALESCE(ec.enabled, TRUE) = TRUE
    UNION
    SELECT DISTINCT er.tenant_id, er.workspace_id
      FROM extraction_runs er
     WHERE er.cartridge_id = 'sap_successfactors'
       AND er.workspace_id IS NOT NULL
),
payload AS (
    SELECT
        s.tenant_id,
        s.workspace_id,
        'sap_successfactors'::text AS cartridge_id,
        'sap_successfactors_talent_monitor'::text AS slug,
        'Talent AgentOps Monitor'::text AS name,
        'Monitor programado de WB-TALENTO con evidencia agregada y recommendation_only.'::text AS description,
        $$Eres el monitor operativo de SuccessFactors Talent. Usa solo evidencia agregada, no expongas PII, no escribas en SuccessFactors y mantén todas las salidas en recommendation_only.$$::text AS instructions,
        'Operativo, sobrio y auditable. Idioma del usuario.'::text AS personality,
        '[
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "mcp-infra__calibration__bayesian_state",
          "refinement__query_dataset",
          "refinement__get_schema"
        ]'::jsonb AS allowed_tools,
        '{"cartridges":["sap_successfactors"],"kinds":["document","schema"]}'::jsonb AS rag_filter,
        'claude-sonnet-4-6'::text AS model,
        2400::int AS max_tokens,
        0.2::real AS temperature,
        $json$
        {
          "role": "monitor",
          "category": "control_room",
          "scope": "workspace",
          "variables": {},
          "schedule": {
            "enabled": true,
            "cron": "*/15 * * * *",
            "tz": "UTC",
            "prompt": "Ejecuta el monitor WB-TALENTO con evidencia agregada y recommendation_only."
          },
          "monitor": {
            "engine": "wisdom_bit",
            "wisdom_bit_id": "WB-TALENTO",
            "dataset": "sap_successfactors_talent_operational_features",
            "threshold": {
              "status_not_in": ["ready"],
              "min_signal_count": 1,
              "blockers_present": true
            },
            "severity": "medium",
            "dedup_key": "sap_successfactors:WB-TALENTO:workspace",
            "recommended_action": "Revisar blockers C/P/A y priorizar acciones supervisadas en Control Room.",
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
                  "decision_mode": "recommendation_only"
                },
                "evidence_refs": [
                  {"type": "wisdom_bit", "id": "WB-TALENTO"}
                ]
              },
              {
                "name": "bayesian_calibration",
                "enabled": true,
                "calibration_group": "sap_successfactors:talent_readiness",
                "model_version": "bayesian_calibration.v1",
                "limit": 10,
                "assumptions": {
                  "basis": "Estado historico agregado de readiness de talento.",
                  "privacy": "Sin full_name, user_id, PERNR, salario ni payCompValue.",
                  "decision_mode": "recommendation_only"
                },
                "evidence_refs": [
                  {"type": "calibration_group", "id": "sap_successfactors:talent_readiness"}
                ]
              },
              {
                "name": "decision_orchestrator",
                "enabled": true,
                "source_type": "wisdom_bit",
                "source_id": "WB-TALENTO",
                "title": "Decision operativa WB-TALENTO",
                "description": "Evaluar senales agregadas de talento con seguimiento supervisado.",
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
                      "decision_mode": "recommendation_only"
                    },
                    "evidence_refs": [
                      {"type": "wisdom_bit", "id": "WB-TALENTO"}
                    ]
                  },
                  "bayesian_calibration": {
                    "calibration_group": "sap_successfactors:talent_readiness",
                    "model_version": "bayesian_calibration.v1",
                    "limit": 10
                  }
                }
              }
            ]
          }
        }
        $json$::jsonb AS extra
    FROM active_scope s
),
updated AS (
    UPDATE agents a
       SET extra = p.extra,
           allowed_tools = p.allowed_tools,
           rag_filter = p.rag_filter,
           description = p.description,
           instructions = p.instructions,
           personality = p.personality,
           model = p.model,
           max_tokens = p.max_tokens,
           temperature = p.temperature,
           is_active = TRUE,
           updated_at = NOW()
      FROM payload p
     WHERE a.workspace_id = p.workspace_id
       AND a.cartridge_id = p.cartridge_id
       AND a.slug = p.slug
     RETURNING a.workspace_id
)
INSERT INTO agents (
    tenant_id, workspace_id, cartridge_id, slug, name, description,
    instructions, personality, allowed_tools, rag_filter, model,
    max_tokens, temperature, extra, is_active
)
SELECT
    p.tenant_id, p.workspace_id, p.cartridge_id, p.slug, p.name, p.description,
    p.instructions, p.personality, p.allowed_tools, p.rag_filter, p.model,
    p.max_tokens, p.temperature, p.extra, TRUE
FROM payload p
WHERE NOT EXISTS (
    SELECT 1
      FROM updated u
     WHERE u.workspace_id = p.workspace_id
);

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zm_sap_successfactors_talent_operational_activation.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

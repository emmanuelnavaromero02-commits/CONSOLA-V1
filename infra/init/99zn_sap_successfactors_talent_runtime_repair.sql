-- 99zn_sap_successfactors_talent_runtime_repair.sql
--
-- Idempotent repair for environments where WB-TALENTO was deployed with the
-- operational feature pack but the active runtime still had an incomplete
-- monitor contract or an old disabled benchmark SQL definition.

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

WITH contract AS (
    SELECT
        '[
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "mcp-infra__calibration__bayesian_state",
          "refinement__query_dataset",
          "refinement__get_schema"
        ]'::jsonb AS allowed_tools,
        '[
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
        ]'::jsonb AS engines
)
UPDATE agents a
   SET allowed_tools = contract.allowed_tools,
       extra = jsonb_set(
           jsonb_set(
               COALESCE(a.extra, '{}'::jsonb),
               '{monitor,dataset}',
               '"sap_successfactors_talent_operational_features"'::jsonb,
               true
           ),
           '{monitor,engines}',
           contract.engines,
           true
       ),
       is_active = TRUE,
       updated_at = NOW()
  FROM contract
 WHERE a.cartridge_id = 'sap_successfactors'
   AND a.slug = 'sap_successfactors_talent_monitor';

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zn_sap_successfactors_talent_runtime_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

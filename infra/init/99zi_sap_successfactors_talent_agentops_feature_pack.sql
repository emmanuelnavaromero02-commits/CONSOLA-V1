-- 99zi_sap_successfactors_talent_agentops_feature_pack.sql
--
-- Existing workspaces may already have the WB-TALENTO monitor from 99t.
-- Refresh only the monitor engine contract so AgentOps reads the scoped Gold
-- feature inputs instead of static demo variables.

UPDATE agents
   SET extra = jsonb_set(
       jsonb_set(
         COALESCE(extra, '{}'::jsonb),
         '{monitor,dataset}',
         '"sap_successfactors_talent_operational_features"'::jsonb,
         true
       ),
       '{monitor,engines}',
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
           "ready_statuses": ["ready", "partial", "benchmark_internal"],
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
             "bayesian_calibration": {
               "calibration_group": "sap_successfactors:talent_readiness",
               "model_version": "bayesian_calibration.v1",
               "limit": 10
             }
           }
         }
       ]'::jsonb,
       true
     ),
       updated_at = NOW()
 WHERE cartridge_id = 'sap_successfactors'
   AND slug = 'sap_successfactors_talent_monitor';

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zi_sap_successfactors_talent_agentops_feature_pack.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

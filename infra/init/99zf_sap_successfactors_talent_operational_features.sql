-- 99zf_sap_successfactors_talent_operational_features.sql
--
-- Register the aggregate Talent feature pack used by Control Room and
-- supervised analysis. The datasets are Gold, aggregate-only and do not expose
-- PII, compensation or write-back instructions.

INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id)
VALUES
($seed$sap_successfactors_talent_operational_features$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$[
  "gold/sap_successfactors/sap_successfactors_talent_employee_profile",
  "gold/sap_successfactors/sap_successfactors_talent_role_profile",
  "gold/sap_successfactors/sap_successfactors_talent_readiness",
  "gold/sap_successfactors/sap_successfactors_talent_9box",
  "gold/sap_successfactors/sap_successfactors_talent_9box_operational",
  "gold/sap_successfactors/sap_successfactors_talent_mobility_history",
  "gold/sap_successfactors/sap_successfactors_talent_signals"
]$seed$::jsonb, $sql$
-- sap_successfactors_talent_operational_features  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_9box_operational", "gold/sap_successfactors/sap_successfactors_talent_mobility_history", "gold/sap_successfactors/sap_successfactors_talent_signals"]
-- description: Feature pack agregado para WB-TALENTO. No expone PII ni nombres de motores; sirve como contrato operativo para Control Room.

WITH employee_profile AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
role_profile AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
nine_box AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
nine_box_operational AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box_operational/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
mobility AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
signals AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_signals/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM employee_profile) AS employee_count,
        (SELECT COUNT(*) FROM employee_profile WHERE profile_status IN ('foundation_ready', 'ready', 'partial')) AS profiled_employee_count,
        (SELECT COUNT(*) FROM employee_profile WHERE cpa_status NOT IN ('insufficient_data', 'blocked', 'missing')) AS cpa_employee_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'ready') AS readiness_high_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'near') AS readiness_medium_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'not_ready') AS readiness_low_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status IN ('insufficient_data', 'blocked', 'missing')) AS readiness_pending_count,
        (SELECT COUNT(*) FROM nine_box WHERE box_status = 'ready') AS nine_box_classified_count,
        (SELECT COUNT(*) FROM nine_box WHERE box_status != 'ready') AS nine_box_pending_count,
        (SELECT COUNT(*) FROM role_profile) AS role_count,
        (SELECT COUNT(*) FROM role_profile WHERE required_skills_status IN ('blocked', 'insufficient_data', 'missing')) AS roles_without_requirements_count,
        (SELECT COUNT(*) FROM signals) AS open_signal_count,
        (SELECT COUNT(*) FROM signals WHERE severity IN ('critical', 'high')) AS high_severity_signal_count,
        (SELECT COUNT(*) FROM signals WHERE LOWER(signal_id) LIKE '%learning%' OR LOWER(signal_type) LIKE '%learning%') AS learning_blocker_count,
        (SELECT COUNT(*) FROM signals WHERE LOWER(signal_id) LIKE '%recruit%' OR LOWER(signal_type) LIKE '%recruit%') AS recruiting_blocker_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS mobility_observed_count,
        (SELECT COALESCE(SUM(ready_count), 0) FROM nine_box_operational) AS nine_box_operational_ready_count
),
scored AS (
    SELECT
        *,
        CASE
            WHEN employee_count = 0 THEN 0.0
            ELSE ROUND((cpa_employee_count::DOUBLE / NULLIF(employee_count, 0)) * 100, 2)
        END AS cpa_coverage_pct,
        CASE
            WHEN role_count = 0 THEN 0.0
            ELSE ROUND(((role_count - roles_without_requirements_count)::DOUBLE / NULLIF(role_count, 0)) * 100, 2)
        END AS role_requirements_coverage_pct,
        CASE
            WHEN employee_count = 0 THEN 0.0
            ELSE ROUND((nine_box_classified_count::DOUBLE / NULLIF(employee_count, 0)) * 100, 2)
        END AS nine_box_coverage_pct
    FROM metrics
)
SELECT
    'WB-TALENTO' AS source_id,
    employee_count,
    profiled_employee_count,
    cpa_employee_count AS calculable_employee_count,
    readiness_high_count,
    readiness_medium_count,
    readiness_low_count,
    readiness_pending_count,
    nine_box_classified_count,
    nine_box_pending_count,
    nine_box_operational_ready_count,
    role_count,
    roles_without_requirements_count,
    open_signal_count,
    high_severity_signal_count,
    learning_blocker_count,
    recruiting_blocker_count,
    mobility_observed_count,
    roles_without_requirements_count AS skill_gap_count,
    cpa_coverage_pct AS skill_coverage_pct,
    role_requirements_coverage_pct,
    nine_box_coverage_pct,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN cpa_employee_count = 0 AND roles_without_requirements_count > 0 THEN 'partial'
        WHEN cpa_employee_count = 0 THEN 'partial'
        WHEN open_signal_count > 0 THEN 'ready'
        ELSE 'ready'
    END AS feature_status,
    CASE
        WHEN employee_count = 0 THEN 'En espera de datos'
        WHEN cpa_employee_count = 0 AND roles_without_requirements_count > 0 THEN 'Datos parciales'
        WHEN cpa_employee_count = 0 THEN 'Requiere historial adicional'
        WHEN open_signal_count > 0 THEN 'Analisis listo'
        ELSE 'Preparando analisis'
    END AS user_status_label,
    CASE
        WHEN employee_count = 0 THEN '["sin_datos_materializados"]'
        WHEN cpa_employee_count = 0 AND roles_without_requirements_count > 0 THEN '["datos_de_talento_pendientes","requisitos_de_rol_pendientes"]'
        WHEN cpa_employee_count = 0 THEN '["datos_de_talento_pendientes"]'
        WHEN roles_without_requirements_count > 0 THEN '["requisitos_de_rol_pendientes"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM scored
$sql$, $seed$Feature pack agregado para WB-TALENTO y Control Room. No expone PII ni nombres de motores.$seed$, $seed${}$seed$::jsonb, NULL, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)),
($seed$sap_successfactors_talent_simulation_inputs$seed$, $seed$gold$seed$, $seed$sap_successfactors$seed$, $seed$[
  "gold/sap_successfactors/sap_successfactors_talent_operational_features"
]$seed$::jsonb, $sql$
-- sap_successfactors_talent_simulation_inputs  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_operational_features"]
-- description: Variables agregadas internas para analisis WB-TALENTO. No expone PII ni nombres de motores al usuario final.

WITH features AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_operational_features/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    source_id,
    CASE
        WHEN feature_status = 'ready' THEN 'ready'
        WHEN feature_status = 'partial' THEN 'partial'
        ELSE 'blocked'
    END AS input_status,
    user_status_label,
    employee_count,
    calculable_employee_count,
    readiness_high_count,
    readiness_medium_count,
    readiness_low_count,
    readiness_pending_count,
    nine_box_classified_count,
    roles_without_requirements_count,
    open_signal_count,
    high_severity_signal_count,
    learning_blocker_count,
    recruiting_blocker_count,
    mobility_observed_count,
    skill_gap_count,
    skill_coverage_pct,
    role_requirements_coverage_pct,
    nine_box_coverage_pct,
    '[' ||
      '{"name":"employee_count","value":' || CAST(employee_count AS VARCHAR) || '},' ||
      '{"name":"calculable_employee_count","value":' || CAST(calculable_employee_count AS VARCHAR) || '},' ||
      '{"name":"readiness_pending_count","value":' || CAST(readiness_pending_count AS VARCHAR) || '},' ||
      '{"name":"roles_without_requirements_count","value":' || CAST(roles_without_requirements_count AS VARCHAR) || '},' ||
      '{"name":"open_signal_count","value":' || CAST(open_signal_count AS VARCHAR) || '},' ||
      '{"name":"skill_coverage_pct","value":' || CAST(skill_coverage_pct AS VARCHAR) || '}' ||
    ']' AS variables_json,
    '[' ||
      '{"source_dataset":"sap_successfactors_talent_operational_features","row_count":1},' ||
      '{"source_dataset":"sap_successfactors_talent_signals","row_count":' || CAST(open_signal_count AS VARCHAR) || '}' ||
    ']' AS evidence_json,
    blockers,
    CASE
        WHEN feature_status = 'ready' THEN 'Variables agregadas listas para analisis supervisado.'
        WHEN feature_status = 'partial' THEN 'Variables agregadas parciales; usar blockers antes de decidir.'
        ELSE 'Sin variables suficientes para analisis supervisado.'
    END AS assumptions,
    CASE
        WHEN employee_count >= 1 THEN 3
        ELSE 0
    END AS scenario_count,
    'talent_operational_features.v1' AS analysis_contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM features
$sql$, $seed$Variables agregadas internas para analisis supervisado WB-TALENTO.$seed$, $seed${}$seed$::jsonb, NULL, NOW(), (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1))
ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zf_sap_successfactors_talent_operational_features.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

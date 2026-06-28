-- 99zj_sap_successfactors_talent_operational_contract_v2.sql
--
-- Upgrade WB-TALENTO operational datasets to the v2 aggregate contract.
-- All datasets are Gold, aggregate-only and workspace scoped at materialization
-- time by DuckDBEngine.

WITH target_workspace AS (
    SELECT id, tenant_id
      FROM workspaces
     ORDER BY created_at ASC
     LIMIT 1
),
ranked_unscoped AS (
    SELECT d.ctid,
           d.name,
           ROW_NUMBER() OVER (PARTITION BY d.name ORDER BY d.updated_at DESC NULLS LAST, d.ctid) AS rn
      FROM datasets d
     WHERE d.cartridge = 'sap_successfactors'
       AND d.workspace_id IS NULL
       AND d.name IN (
            'sap_successfactors_talent_benchmark_internal',
            'sap_successfactors_talent_operational_features',
            'sap_successfactors_talent_simulation_inputs'
       )
),
promoted AS (
    UPDATE datasets d
       SET workspace_id = tw.id,
           tenant_id = COALESCE(d.tenant_id, tw.tenant_id),
           scope_status = 'scoped',
           updated_at = NOW()
      FROM ranked_unscoped ru
      JOIN target_workspace tw ON TRUE
     WHERE d.ctid = ru.ctid
       AND ru.rn = 1
       AND NOT EXISTS (
            SELECT 1
              FROM datasets existing
             WHERE existing.workspace_id = tw.id
               AND existing.name = d.name
       )
     RETURNING d.name
)
DELETE FROM datasets d
 USING target_workspace tw
 WHERE d.cartridge = 'sap_successfactors'
   AND d.workspace_id IS NULL
   AND d.name IN (
        'sap_successfactors_talent_benchmark_internal',
        'sap_successfactors_talent_operational_features',
        'sap_successfactors_talent_simulation_inputs'
   );

WITH target_workspace AS (
    SELECT id, tenant_id
      FROM workspaces
     ORDER BY created_at ASC
     LIMIT 1
)
INSERT INTO datasets (name, layer, cartridge, sources, sql_def, description, column_mapping, schedule, updated_at, workspace_id, tenant_id, scope_status)
SELECT seed.name,
       seed.layer,
       seed.cartridge,
       seed.sources,
       seed.sql_def,
       seed.description,
       seed.column_mapping,
       seed.schedule,
       NOW(),
       tw.id,
       tw.tenant_id,
       'scoped'
  FROM target_workspace tw
 CROSS JOIN (
    VALUES
    ('sap_successfactors_talent_benchmark_internal', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Benchmark interno Talent aprobado para fallback operativo.', '{}'::jsonb, NULL),
    ('sap_successfactors_talent_operational_features', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Feature pack agregado WB-TALENTO.', '{}'::jsonb, NULL),
    ('sap_successfactors_talent_simulation_inputs', 'gold', 'sap_successfactors', '[]'::jsonb, 'SELECT 1 AS placeholder', 'Inputs agregados WB-TALENTO.', '{}'::jsonb, NULL)
 ) AS seed(name, layer, cartridge, sources, sql_def, description, column_mapping, schedule)
 WHERE NOT EXISTS (
    SELECT 1
      FROM datasets existing
     WHERE existing.workspace_id = tw.id
       AND existing.name = seed.name
 )
ON CONFLICT (workspace_id, name) DO NOTHING;

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

UPDATE datasets
   SET sources = '[
         "gold/sap_successfactors/sap_successfactors_talent_employee_profile",
         "gold/sap_successfactors/sap_successfactors_talent_role_profile",
         "gold/sap_successfactors/sap_successfactors_talent_readiness",
         "gold/sap_successfactors/sap_successfactors_talent_9box",
         "gold/sap_successfactors/sap_successfactors_talent_9box_operational",
         "gold/sap_successfactors/sap_successfactors_talent_mobility_history",
         "gold/sap_successfactors/sap_successfactors_talent_signals",
         "gold/sap_successfactors/sap_successfactors_talent_learning_certification_status",
         "gold/sap_successfactors/sap_successfactors_recruitment_application_funnel",
         "gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap"
       ]'::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_operational_features  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_9box_operational", "gold/sap_successfactors/sap_successfactors_talent_mobility_history", "gold/sap_successfactors/sap_successfactors_talent_signals", "gold/sap_successfactors/sap_successfactors_talent_learning_certification_status", "gold/sap_successfactors/sap_successfactors_recruitment_application_funnel", "gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap"]
-- description: Feature pack agregado para WB-TALENTO. Una fila por workspace/materializacion; sin PII ni nombres de motores.

WITH employee_profile AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
role_profile AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
readiness AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
nine_box AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
nine_box_operational AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box_operational/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
mobility AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
signals AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_signals/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
learning AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_learning_certification_status/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
recruiting AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_recruitment_application_funnel/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
skill_gaps AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
metrics AS (
    SELECT
        (SELECT MAX(CAST(workspace_id AS VARCHAR)) FROM employee_profile) AS workspace_id,
        (SELECT COUNT(*) FROM employee_profile) AS employee_count,
        (SELECT COUNT(*) FROM employee_profile WHERE profile_status IN ('foundation_ready', 'ready', 'partial')) AS profiled_count,
        (SELECT COUNT(*) FROM readiness WHERE source_mode IN ('cpa_real', 'benchmark_internal')) AS calculable_count,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'not_ready') AS readiness_low,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'near') AS readiness_medium,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status = 'ready') AS readiness_high,
        (SELECT COUNT(*) FROM readiness WHERE readiness_status IN ('insufficient_data', 'blocked', 'missing')) AS readiness_pending_count,
        (SELECT COUNT(*) FROM readiness WHERE source_mode = 'cpa_real') AS readiness_cpa_real_count,
        (SELECT COUNT(*) FROM readiness WHERE source_mode = 'benchmark_internal') AS readiness_benchmark_count,
        (SELECT COUNT(*) FROM nine_box WHERE box_status IN ('ready', 'benchmark_internal')) AS nine_box_classified_count,
        (SELECT COUNT(*) FROM nine_box WHERE box_status NOT IN ('ready', 'benchmark_internal')) AS nine_box_blocked_count,
        (SELECT COALESCE(SUM(ready_count), 0) FROM nine_box_operational) AS nine_box_operational_ready_count,
        (SELECT COUNT(*) FROM role_profile) AS role_count,
        (SELECT COUNT(*) FROM role_profile WHERE required_skills_status IN ('blocked', 'insufficient_data', 'missing')) AS roles_without_requirements,
        (SELECT COUNT(*) FROM signals) AS open_signal_count,
        (SELECT COUNT(*) FROM signals WHERE severity = 'critical') AS critical_signal_count,
        (SELECT COUNT(*) FROM signals WHERE severity = 'high') AS high_signal_count,
        (SELECT COUNT(*) FROM signals WHERE severity = 'medium') AS medium_signal_count,
        (SELECT COUNT(*) FROM signals WHERE severity = 'low') AS low_signal_count,
        (SELECT COALESCE(SUM(overdue_events), 0) FROM learning) AS learning_overdue_events,
        (SELECT COUNT(*) FROM learning WHERE learning_kpi_status != 'ready' OR overdue_events > 0) AS learning_blocked_count,
        (SELECT COALESCE(SUM(applications), 0) FROM recruiting) AS recruiting_application_count,
        (SELECT COUNT(*) FROM recruiting WHERE application_funnel_status != 'ready') AS recruiting_blocked_count,
        (SELECT COUNT(*) FROM mobility WHERE movement_events > 0) AS mobility_observed_count,
        (SELECT COUNT(*) FROM skill_gaps WHERE skill_gap_status IN ('insufficient_data', 'partial', 'blocked')) AS skill_gap_count,
        (SELECT COUNT(*) FROM skill_gaps WHERE skill_gap_status = 'ready') AS skill_ready_role_count,
        (SELECT COUNT(*) FROM skill_gaps) AS skill_role_count
),
coverage AS (
    SELECT
        *,
        CASE WHEN employee_count = 0 THEN 0.0 ELSE profiled_count::DOUBLE / NULLIF(employee_count, 0) END AS profiled_ratio,
        CASE WHEN employee_count = 0 THEN 0.0 ELSE calculable_count::DOUBLE / NULLIF(employee_count, 0) END AS readiness_ratio,
        CASE WHEN employee_count = 0 THEN 0.0 ELSE nine_box_classified_count::DOUBLE / NULLIF(employee_count, 0) END AS nine_box_ratio,
        CASE WHEN role_count = 0 THEN 0.0 ELSE (role_count - roles_without_requirements)::DOUBLE / NULLIF(role_count, 0) END AS role_requirements_ratio,
        CASE WHEN skill_role_count = 0 THEN 0.0 ELSE skill_ready_role_count::DOUBLE / NULLIF(skill_role_count, 0) END AS skill_coverage_ratio,
        CASE WHEN learning_overdue_events > 0 OR learning_blocked_count > 0 THEN 0.0 ELSE 1.0 END AS learning_ratio,
        CASE WHEN recruiting_blocked_count > 0 THEN 0.0 ELSE 1.0 END AS recruiting_ratio
    FROM metrics
),
scored AS (
    SELECT
        *,
        ROUND(LEAST(1.0, GREATEST(0.0,
            (0.20 * profiled_ratio) + (0.25 * readiness_ratio) + (0.15 * nine_box_ratio)
            + (0.15 * role_requirements_ratio) + (0.15 * skill_coverage_ratio)
            + (0.05 * learning_ratio) + (0.05 * recruiting_ratio)
        )), 4) AS confidence
    FROM coverage
)
SELECT
    'WB-TALENTO' AS source_id,
    workspace_id,
    CURRENT_TIMESTAMP AS materialized_at,
    employee_count,
    profiled_count,
    profiled_count AS profiled_employee_count,
    calculable_count,
    calculable_count AS calculable_employee_count,
    readiness_low,
    readiness_low AS readiness_low_count,
    readiness_medium,
    readiness_medium AS readiness_medium_count,
    readiness_high,
    readiness_high AS readiness_high_count,
    readiness_pending_count,
    readiness_cpa_real_count,
    readiness_benchmark_count,
    nine_box_classified_count,
    nine_box_blocked_count,
    nine_box_blocked_count AS nine_box_pending_count,
    nine_box_operational_ready_count,
    role_count,
    roles_without_requirements,
    roles_without_requirements AS roles_without_requirements_count,
    open_signal_count,
    high_signal_count + critical_signal_count AS high_severity_signal_count,
    '{"critical":' || CAST(critical_signal_count AS VARCHAR) || ',"high":' || CAST(high_signal_count AS VARCHAR) || ',"medium":' || CAST(medium_signal_count AS VARCHAR) || ',"low":' || CAST(low_signal_count AS VARCHAR) || '}' AS open_signals_by_severity,
    learning_blocked_count,
    recruiting_blocked_count,
    mobility_observed_count,
    skill_gap_count,
    ROUND(skill_coverage_ratio * 100, 2) AS skill_coverage_pct,
    ROUND(role_requirements_ratio * 100, 2) AS role_requirements_coverage_pct,
    ROUND(nine_box_ratio * 100, 2) AS nine_box_coverage_pct,
    confidence,
    CASE
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'benchmark_internal'
        WHEN readiness_cpa_real_count > 0 THEN 'cpa_real'
        ELSE 'insufficient_data'
    END AS source_mode,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'benchmark_internal'
        WHEN calculable_count = 0 THEN 'insufficient_data'
        WHEN roles_without_requirements > 0 OR learning_blocked_count > 0 OR recruiting_blocked_count > 0 OR skill_gap_count > 0 THEN 'partial'
        ELSE 'ready'
    END AS readiness_status,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'benchmark_internal'
        WHEN calculable_count = 0 THEN 'insufficient_data'
        WHEN roles_without_requirements > 0 OR learning_blocked_count > 0 OR recruiting_blocked_count > 0 OR skill_gap_count > 0 THEN 'partial'
        ELSE 'ready'
    END AS feature_status,
    CASE
        WHEN employee_count = 0 THEN 'En espera de datos'
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'Analisis con referencia interna'
        WHEN calculable_count = 0 THEN 'Requiere historial adicional'
        WHEN roles_without_requirements > 0 OR learning_blocked_count > 0 OR recruiting_blocked_count > 0 OR skill_gap_count > 0 THEN 'Datos parciales'
        ELSE 'Analisis listo'
    END AS user_status_label,
    '[' || RTRIM(CONCAT(
        CASE WHEN employee_count = 0 THEN '"sin_datos_materializados",' ELSE '' END,
        CASE WHEN calculable_count = 0 THEN '"talent_cpa_inputs_missing",' ELSE '' END,
        CASE WHEN calculable_count = 0 AND readiness_benchmark_count = 0 THEN '"benchmark_internal_not_configured",' ELSE '' END,
        CASE WHEN roles_without_requirements > 0 THEN '"role_requirements_pending",' ELSE '' END,
        CASE WHEN learning_blocked_count > 0 THEN '"learning_data_partial",' ELSE '' END,
        CASE WHEN recruiting_blocked_count > 0 THEN '"recruiting_data_partial",' ELSE '' END,
        CASE WHEN skill_gap_count > 0 THEN '"skill_gap_data_partial",' ELSE '' END
    ), ',') || ']' AS blockers,
    'talent_operational_features.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM scored
$sql$,
       description = 'Feature pack agregado para WB-TALENTO y Control Room. No expone PII ni nombres de motores.',
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_operational_features'
   AND cartridge = 'sap_successfactors';

UPDATE datasets
   SET sources = '["gold/sap_successfactors/sap_successfactors_talent_operational_features"]'::jsonb,
       sql_def = $sql$
-- sap_successfactors_talent_simulation_inputs  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_operational_features"]
-- description: Variables agregadas internas para analisis WB-TALENTO. No expone PII ni nombres tecnicos al usuario final.

WITH features AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_operational_features/**/*.parquet', hive_partitioning = true, union_by_name = true)
),
scored AS (
    SELECT
        *,
        CASE
            WHEN employee_count = 0 THEN 100.0
            ELSE LEAST(100.0, GREATEST(0.0,
                ((1.0 - COALESCE(confidence, 0.0)) * 40.0)
                + ((readiness_pending_count::DOUBLE / NULLIF(employee_count, 0)) * 25.0)
                + ((nine_box_blocked_count::DOUBLE / NULLIF(employee_count, 0)) * 15.0)
                + ((roles_without_requirements::DOUBLE / NULLIF(GREATEST(role_count, 1), 0)) * 10.0)
                + (LEAST(high_severity_signal_count, 10) * 1.0)
                + (LEAST(skill_gap_count, 10) * 1.0)
            ))
        END AS riesgo_base,
        LEAST(1.0, GREATEST(0.0, 1.0 - COALESCE(confidence, 0.0))) AS incertidumbre,
        CASE
            WHEN employee_count = 0 THEN 0
            WHEN readiness_status IN ('ready', 'benchmark_internal') THEN 3
            ELSE 0
        END AS scenario_count_calc
    FROM features
)
SELECT
    source_id,
    workspace_id,
    CURRENT_TIMESTAMP AS materialized_at,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN scenario_count_calc = 0 THEN 'blocked'
        WHEN readiness_status IN ('ready', 'benchmark_internal') THEN 'ready'
        WHEN readiness_status = 'partial' THEN 'partial'
        ELSE 'blocked'
    END AS input_status,
    CASE
        WHEN employee_count = 0 THEN 'En espera de datos'
        WHEN scenario_count_calc = 0 THEN 'Requiere historial adicional'
        WHEN readiness_status = 'benchmark_internal' THEN 'Analisis con referencia interna'
        WHEN readiness_status = 'partial' THEN 'Datos parciales'
        WHEN readiness_status = 'ready' THEN 'Analisis listo'
        ELSE 'En espera de datos'
    END AS user_status_label,
    employee_count,
    profiled_count,
    calculable_count,
    readiness_high,
    readiness_medium,
    readiness_low,
    readiness_pending_count,
    nine_box_classified_count,
    nine_box_blocked_count,
    roles_without_requirements AS roles_bloqueados,
    roles_without_requirements,
    open_signal_count AS senales_abiertas,
    open_signal_count,
    high_severity_signal_count,
    learning_blocked_count,
    recruiting_blocked_count,
    mobility_observed_count,
    skill_gap_count,
    skill_coverage_pct AS cobertura_skill,
    skill_coverage_pct,
    role_requirements_coverage_pct,
    nine_box_coverage_pct,
    confidence,
    readiness_status,
    source_mode,
    ROUND(riesgo_base, 2) AS riesgo_base,
    ROUND(incertidumbre, 4) AS incertidumbre,
    scenario_count_calc AS scenario_count,
    '{'
      || '"baseline_value":{"type":"fixed","value":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},'
      || '"expected_delta":{"type":"triangular","low":' || CAST(ROUND(-1.0 * riesgo_base, 2) AS VARCHAR)
      || ',"mode":' || CAST(ROUND(-0.35 * riesgo_base, 2) AS VARCHAR)
      || ',"high":' || CAST(ROUND(0.10 * riesgo_base, 2) AS VARCHAR) || '},'
      || '"delay_days":{"type":"triangular","low":0,"mode":' || CAST(CASE WHEN high_severity_signal_count > 0 THEN 7 ELSE 3 END AS VARCHAR)
      || ',"high":' || CAST(CASE WHEN high_severity_signal_count > 0 THEN 21 ELSE 10 END AS VARCHAR) || '},'
      || '"cost_per_day":{"type":"fixed","value":1},'
      || '"probability_of_delay":{"type":"triangular","low":0.10,"mode":'
      || CAST(ROUND(LEAST(0.85, GREATEST(0.20, incertidumbre)), 2) AS VARCHAR)
      || ',"high":0.95}'
      || '}' AS input_variables_json,
    '[' ||
      '{"name":"riesgo_base","value":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},' ||
      '{"name":"empleados_sin_readiness","value":' || CAST(readiness_pending_count AS VARCHAR) || '},' ||
      '{"name":"roles_bloqueados","value":' || CAST(roles_without_requirements AS VARCHAR) || '},' ||
      '{"name":"senales_abiertas","value":' || CAST(open_signal_count AS VARCHAR) || '},' ||
      '{"name":"cobertura_skill","value":' || CAST(ROUND(skill_coverage_pct, 2) AS VARCHAR) || '},' ||
      '{"name":"incertidumbre","value":' || CAST(ROUND(incertidumbre, 4) AS VARCHAR) || '}' ||
    ']' AS variables_json,
    '[' ||
      '{"source_dataset":"sap_successfactors_talent_operational_features","row_count":1},' ||
      '{"source_dataset":"sap_successfactors_talent_signals","row_count":' || CAST(open_signal_count AS VARCHAR) || '},' ||
      '{"source_dataset":"sap_successfactors_talent_readiness","row_count":' || CAST(calculable_count + readiness_pending_count AS VARCHAR) || '},' ||
      '{"source_dataset":"sap_successfactors_talent_9box","row_count":' || CAST(nine_box_classified_count + nine_box_blocked_count AS VARCHAR) || '}' ||
    ']' AS evidence_json,
    '[{"type":"gold_dataset","id":"sap_successfactors_talent_operational_features"},{"type":"wisdom_bit","id":"WB-TALENTO"}]' AS evidence_refs_json,
    '{'
      || '"basis":"Agregado WB-TALENTO sin PII",'
      || '"decision_mode":"recommendation_only",'
      || '"source_mode":"' || source_mode || '",'
      || '"readiness_status":"' || readiness_status || '",'
      || '"contract_version":"' || contract_version || '"'
      || '}' AS assumptions_json,
    '[' ||
      '{"name":"base","risk":' || CAST(ROUND(riesgo_base, 2) AS VARCHAR) || '},' ||
      '{"name":"conservative","risk":' || CAST(ROUND(LEAST(100.0, riesgo_base + (incertidumbre * 20.0)), 2) AS VARCHAR) || '},' ||
      '{"name":"improved","risk":' || CAST(ROUND(GREATEST(0.0, riesgo_base - 15.0), 2) AS VARCHAR) || '}' ||
    ']' AS escenarios,
    blockers,
    CASE
        WHEN scenario_count_calc = 0 THEN 'missing_simulation_inputs'
        WHEN readiness_status = 'partial' THEN 'missing_simulation_inputs'
        WHEN readiness_status = 'insufficient_data' THEN 'missing_simulation_inputs'
        ELSE NULL
    END AS blocked_reason,
    'talent_simulation_inputs.v2' AS analysis_contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM scored
$sql$,
       description = 'Variables agregadas internas para analisis supervisado WB-TALENTO.',
       updated_at = NOW()
 WHERE name = 'sap_successfactors_talent_simulation_inputs'
   AND cartridge = 'sap_successfactors';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zj_sap_successfactors_talent_operational_contract_v2.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

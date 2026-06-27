-- sap_successfactors_talent_operational_features  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile", "gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_9box", "gold/sap_successfactors/sap_successfactors_talent_9box_operational", "gold/sap_successfactors/sap_successfactors_talent_mobility_history", "gold/sap_successfactors/sap_successfactors_talent_signals", "gold/sap_successfactors/sap_successfactors_talent_learning_certification_status", "gold/sap_successfactors/sap_successfactors_recruitment_application_funnel", "gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap"]
-- description: Feature pack agregado para WB-TALENTO. Una fila por workspace/materializacion; sin PII ni nombres de motores.

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
learning AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_learning_certification_status/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
recruiting AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_recruitment_application_funnel/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
skill_gaps AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_competency_skill_gap/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
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
            (0.20 * profiled_ratio)
            + (0.25 * readiness_ratio)
            + (0.15 * nine_box_ratio)
            + (0.15 * role_requirements_ratio)
            + (0.15 * skill_coverage_ratio)
            + (0.05 * learning_ratio)
            + (0.05 * recruiting_ratio)
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
    '{"critical":' || CAST(critical_signal_count AS VARCHAR)
      || ',"high":' || CAST(high_signal_count AS VARCHAR)
      || ',"medium":' || CAST(medium_signal_count AS VARCHAR)
      || ',"low":' || CAST(low_signal_count AS VARCHAR)
      || '}' AS open_signals_by_severity,
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
        WHEN roles_without_requirements > 0
          OR learning_blocked_count > 0
          OR recruiting_blocked_count > 0
          OR skill_gap_count > 0 THEN 'partial'
        ELSE 'ready'
    END AS readiness_status,
    CASE
        WHEN employee_count = 0 THEN 'blocked'
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'benchmark_internal'
        WHEN calculable_count = 0 THEN 'insufficient_data'
        WHEN roles_without_requirements > 0
          OR learning_blocked_count > 0
          OR recruiting_blocked_count > 0
          OR skill_gap_count > 0 THEN 'partial'
        ELSE 'ready'
    END AS feature_status,
    CASE
        WHEN employee_count = 0 THEN 'En espera de datos'
        WHEN readiness_benchmark_count > 0 AND readiness_cpa_real_count = 0 THEN 'Analisis con referencia interna'
        WHEN calculable_count = 0 THEN 'Requiere historial adicional'
        WHEN roles_without_requirements > 0
          OR learning_blocked_count > 0
          OR recruiting_blocked_count > 0
          OR skill_gap_count > 0 THEN 'Datos parciales'
        ELSE 'Analisis listo'
    END AS user_status_label,
    '[' || RTRIM(
        CONCAT(
            CASE WHEN employee_count = 0 THEN '"sin_datos_materializados",' ELSE '' END,
            CASE WHEN calculable_count = 0 THEN '"talent_cpa_inputs_missing",' ELSE '' END,
            CASE WHEN calculable_count = 0 AND readiness_benchmark_count = 0 THEN '"benchmark_internal_not_configured",' ELSE '' END,
            CASE WHEN roles_without_requirements > 0 THEN '"role_requirements_pending",' ELSE '' END,
            CASE WHEN learning_blocked_count > 0 THEN '"learning_data_partial",' ELSE '' END,
            CASE WHEN recruiting_blocked_count > 0 THEN '"recruiting_data_partial",' ELSE '' END,
            CASE WHEN skill_gap_count > 0 THEN '"skill_gap_data_partial",' ELSE '' END
        ),
        ','
    ) || ']' AS blockers,
    'talent_operational_features.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM scored

-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_cpa_scores", "gold/sap_successfactors/sap_successfactors_talent_benchmark_internal"]
-- description: Readiness con C/P/A observado o percentiles internos; el benchmark sólo es utilizable con aprobación durable registrada por servidor.

WITH cpa AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_cpa_scores/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
),
benchmark AS (
    SELECT *,
        CASE
            WHEN approved = TRUE
              AND TRY_CAST(approved_by AS BIGINT) > 0
              AND TRY_CAST(approved_at AS TIMESTAMP) IS NOT NULL
              AND approval_actor_source = 'server'
              AND approval_recorded_by_server = TRUE
              AND NULLIF(TRIM(CAST(approval_evidence_ref AS VARCHAR)), '') IS NOT NULL
              AND NULLIF(TRIM(CAST(approval_authorization_ref AS VARCHAR)), '') IS NOT NULL
              AND approval_authorization_verified = TRUE
            THEN TRUE ELSE FALSE
        END AS approval_valid
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_benchmark_internal/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    WHERE enabled = TRUE
    ORDER BY approval_valid DESC, materialized_at DESC NULLS LAST
    LIMIT 1
),
benchmark_raw AS (
    SELECT
        cpa.*,
        COALESCE(benchmark.enabled, FALSE) AS benchmark_enabled,
        COALESCE(benchmark.approval_valid, FALSE) AS benchmark_approval_valid,
        benchmark.benchmark_version,
        benchmark.contract_version AS benchmark_contract_version,
        benchmark.readiness_high_threshold,
        benchmark.readiness_medium_threshold,
        CASE
            WHEN cpa.fit_score IS NOT NULL THEN NULL
            WHEN NOT COALESCE(benchmark.approval_valid, FALSE) THEN NULL
            WHEN benchmark.role_coverage_weight IS NULL
              OR benchmark.tenure_weight IS NULL
              OR benchmark.competency_weight IS NULL THEN NULL
            WHEN cpa.required_skills_status IS NULL
              OR cpa.required_skills_status IN ('blocked', 'insufficient_data', 'missing') THEN NULL
            WHEN TRY_CAST(cpa.tenure_months AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(cpa.competency_score AS DOUBLE) IS NULL THEN NULL
            ELSE ROUND(LEAST(100.0, GREATEST(0.0,
                benchmark.role_coverage_weight * 100.0
                + benchmark.tenure_weight * LEAST(
                    100.0,
                    GREATEST(0.0, TRY_CAST(cpa.tenure_months AS DOUBLE) / 36.0 * 100.0)
                )
                + benchmark.competency_weight * LEAST(
                    100.0,
                    GREATEST(
                        0.0,
                        CASE WHEN TRY_CAST(cpa.competency_score AS DOUBLE) <= 5
                            THEN TRY_CAST(cpa.competency_score AS DOUBLE) * 20.0
                            ELSE TRY_CAST(cpa.competency_score AS DOUBLE)
                        END
                    )
                )
            )), 2)
        END AS benchmark_raw_score,
        CASE
            WHEN cpa.fit_score IS NOT NULL THEN 1.0
            WHEN cpa.required_skills_status NOT IN ('blocked', 'insufficient_data', 'missing')
              AND TRY_CAST(cpa.tenure_months AS DOUBLE) IS NOT NULL
              AND TRY_CAST(cpa.competency_score AS DOUBLE) IS NOT NULL THEN 1.0
            ELSE 0.0
        END AS benchmark_input_available
    FROM cpa
    LEFT JOIN benchmark ON TRUE
),
cohort_stats AS (
    SELECT *,
        COUNT(*) OVER (
            PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), ''), COALESCE(CAST(workspace_id AS VARCHAR), '')
        ) AS workspace_employee_count,
        AVG(benchmark_input_available) OVER (
            PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), ''), COALESCE(CAST(workspace_id AS VARCHAR), '')
        ) AS benchmark_input_coverage
    FROM benchmark_raw
),
valid_ranked AS (
    SELECT tenant_id, workspace_id, user_id,
        PERCENT_RANK() OVER (
            PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), ''), COALESCE(CAST(workspace_id AS VARCHAR), '')
            ORDER BY benchmark_raw_score, COALESCE(CAST(job_code AS VARCHAR), ''), COALESCE(CAST(user_id AS VARCHAR), '')
        ) AS benchmark_percentile
    FROM benchmark_raw
    WHERE benchmark_raw_score IS NOT NULL
),
cohort AS (
    SELECT stats.*, ranked.benchmark_percentile
    FROM cohort_stats stats
    LEFT JOIN valid_ranked ranked
      ON ranked.tenant_id IS NOT DISTINCT FROM stats.tenant_id
     AND ranked.workspace_id IS NOT DISTINCT FROM stats.workspace_id
     AND ranked.user_id IS NOT DISTINCT FROM stats.user_id
),
classified AS (
    SELECT *,
        CASE
            WHEN COALESCE(invalid_score_input, FALSE) THEN 'insufficient_data'
            WHEN fit_score IS NOT NULL THEN 'cpa_real'
            WHEN benchmark_approval_valid
              AND benchmark_raw_score IS NOT NULL
              AND workspace_employee_count >= 50
              AND benchmark_input_coverage >= 0.80 THEN 'benchmark_internal'
            ELSE 'insufficient_data'
        END AS source_mode,
        CASE
            WHEN COALESCE(invalid_score_input, FALSE) THEN NULL
            WHEN fit_score IS NOT NULL THEN fit_score
            WHEN benchmark_approval_valid
              AND benchmark_raw_score IS NOT NULL
              AND workspace_employee_count >= 50
              AND benchmark_input_coverage >= 0.80
                THEN ROUND(benchmark_percentile * 100.0, 2)
            ELSE NULL
        END AS readiness_score
    FROM cohort
),
with_blockers AS (
    SELECT *, list_filter([
        CASE WHEN source_mode = 'insufficient_data' AND fit_score IS NULL
                  AND NOT COALESCE(invalid_score_input, FALSE)
            THEN 'talent_cpa_inputs_missing' END,
        CASE WHEN COALESCE(invalid_score_input, FALSE)
            THEN 'talent_score_input_invalid' END,
        CASE WHEN source_mode = 'insufficient_data' AND NOT benchmark_approval_valid
            THEN 'benchmark_internal_not_reviewed' END,
        CASE WHEN source_mode = 'insufficient_data' AND benchmark_approval_valid
                   AND benchmark_raw_score IS NULL
            THEN 'benchmark_observed_inputs_missing' END,
        CASE WHEN source_mode = 'insufficient_data' AND benchmark_approval_valid
                   AND workspace_employee_count < 50
            THEN 'benchmark_min_population_not_met' END,
        CASE WHEN source_mode = 'insufficient_data' AND benchmark_approval_valid
                   AND benchmark_input_coverage < 0.80
            THEN 'benchmark_min_coverage_not_met' END,
        CASE WHEN source_mode <> 'insufficient_data' AND required_skills_status = 'blocked'
            THEN 'role_requirements_pending' END
    ], item -> item IS NOT NULL) AS blocker_items
    FROM classified
)
SELECT
    tenant_id, workspace_id, user_id, full_name, company_name, department_name,
    location_name, job_code, direct_reports, tenure_months, role_name,
    competency_score, performance_score, aspiration_score, fit_score,
    COALESCE(invalid_score_input, FALSE) AS invalid_score_input,
    benchmark_raw_score,
    CASE WHEN benchmark_approval_valid THEN ROUND(benchmark_percentile * 100.0, 2) END AS benchmark_score,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode, benchmark_version, benchmark_approval_valid,
    CASE
        WHEN source_mode = 'cpa_real' THEN 'not_applicable'
        WHEN benchmark_approval_valid THEN 'approved_durable'
        ELSE 'unreviewed'
    END AS benchmark_provenance_status,
    workspace_employee_count,
    ROUND(benchmark_input_coverage, 4) AS benchmark_input_coverage,
    CASE
        WHEN readiness_score IS NULL THEN 'insufficient_data'
        WHEN source_mode = 'benchmark_internal' AND benchmark_percentile >= 0.70 THEN 'ready'
        WHEN source_mode = 'benchmark_internal' AND benchmark_percentile >= 0.30 THEN 'near'
        WHEN source_mode = 'benchmark_internal' THEN 'not_ready'
        WHEN readiness_score >= readiness_high_threshold THEN 'ready'
        WHEN readiness_score >= readiness_medium_threshold THEN 'near'
        ELSE 'not_ready'
    END AS readiness_status,
    CASE
        WHEN readiness_score IS NULL THEN 'Datos insuficientes'
        WHEN source_mode = 'benchmark_internal' THEN 'Referencia interna aprobada'
        WHEN readiness_score >= readiness_high_threshold THEN 'Ready'
        WHEN readiness_score >= readiness_medium_threshold THEN 'Near'
        ELSE 'Not ready'
    END AS readiness_label,
    role_profile_status, required_skills_status,
    list_count(blocker_items) AS blocker_count,
    to_json(blocker_items)::VARCHAR AS blockers,
    CASE
        WHEN source_mode = 'cpa_real' THEN 0.85
        WHEN source_mode = 'benchmark_internal' AND benchmark_approval_valid THEN NULL
        ELSE NULL
    END AS confidence,
    'talent_readiness.v3' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM with_blockers
ORDER BY user_id

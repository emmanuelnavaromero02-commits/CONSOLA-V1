-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_cpa_scores", "gold/sap_successfactors/sap_successfactors_talent_benchmark_internal"]
-- description: Readiness Talento WB-TALENTO. Usa C/P/A real; si falta y hay benchmark interno aprobado, clasifica como benchmark_internal; si no, devuelve insufficient_data.

WITH cpa AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_cpa_scores/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
benchmark AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_benchmark_internal/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE enabled = TRUE
      AND approved = TRUE
    ORDER BY materialized_at DESC NULLS LAST
    LIMIT 1
),
scored AS (
    SELECT
        cpa.*,
        benchmark.benchmark_version,
        benchmark.contract_version AS benchmark_contract_version,
        COALESCE(benchmark.readiness_high_threshold, 80.0) AS readiness_high_threshold,
        COALESCE(benchmark.readiness_medium_threshold, 60.0) AS readiness_medium_threshold,
        CASE
            WHEN cpa.fit_score IS NOT NULL THEN NULL
            WHEN COALESCE(benchmark.enabled, FALSE) IS FALSE
              OR COALESCE(benchmark.approved, FALSE) IS FALSE THEN NULL
            ELSE ROUND(LEAST(100.0, GREATEST(0.0,
                (
                    CASE
                        WHEN cpa.required_skills_status NOT IN ('blocked', 'insufficient_data', 'missing')
                            THEN COALESCE(benchmark.role_coverage_weight, 0.25) * 100.0
                        ELSE COALESCE(benchmark.role_coverage_weight, 0.25) * 35.0
                    END
                )
                + (
                    CASE
                        WHEN TRY_CAST(cpa.tenure_months AS DOUBLE) >= 36 THEN COALESCE(benchmark.tenure_weight, 0.15) * 100.0
                        WHEN TRY_CAST(cpa.tenure_months AS DOUBLE) >= 12 THEN COALESCE(benchmark.tenure_weight, 0.15) * 70.0
                        WHEN TRY_CAST(cpa.tenure_months AS DOUBLE) IS NOT NULL THEN COALESCE(benchmark.tenure_weight, 0.15) * 35.0
                        ELSE 0.0
                    END
                )
                + (
                    CASE
                        WHEN TRY_CAST(cpa.competency_score AS DOUBLE) IS NOT NULL
                            THEN COALESCE(benchmark.competency_weight, 0.60)
                              * CASE
                                    WHEN TRY_CAST(cpa.competency_score AS DOUBLE) <= 5
                                        THEN TRY_CAST(cpa.competency_score AS DOUBLE) * 20.0
                                    ELSE TRY_CAST(cpa.competency_score AS DOUBLE)
                                END
                        WHEN TRY_CAST(cpa.direct_reports AS DOUBLE) > 0 THEN COALESCE(benchmark.competency_weight, 0.60) * 45.0
                        ELSE COALESCE(benchmark.competency_weight, 0.60) * 30.0
                    END
                )
            )), 2)
        END AS benchmark_score
    FROM cpa
    LEFT JOIN benchmark ON TRUE
),
classified AS (
    SELECT
        *,
        COALESCE(fit_score, benchmark_score) AS readiness_score,
        CASE
            WHEN fit_score IS NOT NULL THEN 'cpa_real'
            WHEN benchmark_score IS NOT NULL THEN 'benchmark_internal'
            ELSE 'insufficient_data'
        END AS source_mode
    FROM scored
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    direct_reports,
    tenure_months,
    role_name,
    competency_score,
    performance_score,
    aspiration_score,
    fit_score,
    benchmark_score,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode,
    benchmark_version,
    CASE
        WHEN readiness_score IS NULL THEN 'insufficient_data'
        WHEN readiness_score >= readiness_high_threshold THEN 'ready'
        WHEN readiness_score >= readiness_medium_threshold THEN 'near'
        ELSE 'not_ready'
    END AS readiness_status,
    CASE
        WHEN readiness_score IS NULL THEN 'Datos insuficientes'
        WHEN source_mode = 'benchmark_internal' AND readiness_score >= readiness_high_threshold THEN 'Listo con referencia interna'
        WHEN source_mode = 'benchmark_internal' AND readiness_score >= readiness_medium_threshold THEN 'Cercano con referencia interna'
        WHEN source_mode = 'benchmark_internal' THEN 'Bajo con referencia interna'
        WHEN readiness_score >= readiness_high_threshold THEN 'Ready'
        WHEN readiness_score >= readiness_medium_threshold THEN 'Near'
        ELSE 'Not ready'
    END AS readiness_label,
    role_profile_status,
    required_skills_status,
    CASE
        WHEN source_mode = 'insufficient_data' THEN 3
        WHEN required_skills_status = 'blocked' THEN 1
        ELSE 0
    END AS blocker_count,
    CASE
        WHEN source_mode = 'insufficient_data' THEN '["talent_cpa_inputs_missing","benchmark_internal_not_configured"]'
        WHEN required_skills_status = 'blocked' THEN '["role_requirements_pending"]'
        ELSE '[]'
    END AS blockers,
    'talent_readiness.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM classified
ORDER BY user_id

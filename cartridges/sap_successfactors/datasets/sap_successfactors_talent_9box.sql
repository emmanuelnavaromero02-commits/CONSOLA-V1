-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness"]
-- description: 9-box Talento WB-TALENTO. Clasifica con C/P/A real; si se uso benchmark interno aprobado, marca source_mode=benchmark_internal.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        *,
        CASE
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NOT NULL AND TRY_CAST(performance_score AS DOUBLE) > 5
                THEN TRY_CAST(performance_score AS DOUBLE) / 20
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(performance_score AS DOUBLE)
            ELSE NULL
        END AS performance_scale,
        CASE
            WHEN TRY_CAST(competency_score AS DOUBLE) IS NOT NULL OR TRY_CAST(aspiration_score AS DOUBLE) IS NOT NULL THEN
                (
                    0.60 * CASE
                        WHEN TRY_CAST(competency_score AS DOUBLE) IS NULL THEN 0
                        WHEN TRY_CAST(competency_score AS DOUBLE) > 5 THEN TRY_CAST(competency_score AS DOUBLE) / 20
                        ELSE TRY_CAST(competency_score AS DOUBLE)
                    END
                )
                + (
                    0.40 * CASE
                        WHEN TRY_CAST(aspiration_score AS DOUBLE) IS NULL THEN 0
                        WHEN TRY_CAST(aspiration_score AS DOUBLE) > 5 THEN TRY_CAST(aspiration_score AS DOUBLE) / 20
                        ELSE TRY_CAST(aspiration_score AS DOUBLE)
                    END
                )
            ELSE NULL
        END AS potential_scale,
        CASE
            WHEN source_mode = 'benchmark_internal' AND TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL THEN
                ROUND(LEAST(100.0, GREATEST(0.0,
                    (0.60 * TRY_CAST(readiness_score AS DOUBLE))
                    + (0.25 * CASE
                        WHEN TRY_CAST(tenure_months AS DOUBLE) >= 36 THEN 100.0
                        WHEN TRY_CAST(tenure_months AS DOUBLE) >= 12 THEN 65.0
                        WHEN TRY_CAST(tenure_months AS DOUBLE) IS NOT NULL THEN 35.0
                        ELSE 45.0
                    END)
                    + (0.15 * CASE
                        WHEN required_skills_status NOT IN ('blocked', 'insufficient_data', 'missing') THEN 100.0
                        ELSE 40.0
                    END)
                )), 2)
            ELSE NULL
        END AS benchmark_performance_proxy,
        CASE
            WHEN source_mode = 'benchmark_internal' AND TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL THEN
                ROUND(LEAST(100.0, GREATEST(0.0,
                    (0.50 * TRY_CAST(readiness_score AS DOUBLE))
                    + (0.25 * CASE
                        WHEN TRY_CAST(direct_reports AS DOUBLE) >= 5 THEN 100.0
                        WHEN TRY_CAST(direct_reports AS DOUBLE) > 0 THEN 70.0
                        ELSE 35.0
                    END)
                    + (0.15 * CASE
                        WHEN TRY_CAST(tenure_months AS DOUBLE) BETWEEN 12 AND 60 THEN 100.0
                        WHEN TRY_CAST(tenure_months AS DOUBLE) IS NOT NULL THEN 55.0
                        ELSE 45.0
                    END)
                    + (0.10 * CASE
                        WHEN role_profile_status NOT IN ('blocked', 'insufficient_data', 'missing') THEN 100.0
                        ELSE 40.0
                    END)
                )), 2)
            ELSE NULL
        END AS benchmark_potential_proxy
    FROM readiness
),
ranked AS (
    SELECT
        *,
        PERCENT_RANK() OVER (
            PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), ''), COALESCE(CAST(workspace_id AS VARCHAR), '')
            ORDER BY benchmark_performance_proxy NULLS LAST, COALESCE(CAST(job_code AS VARCHAR), ''), COALESCE(CAST(user_id AS VARCHAR), '')
        ) AS benchmark_performance_percentile,
        PERCENT_RANK() OVER (
            PARTITION BY COALESCE(CAST(tenant_id AS VARCHAR), ''), COALESCE(CAST(workspace_id AS VARCHAR), '')
            ORDER BY benchmark_potential_proxy NULLS LAST, COALESCE(CAST(role_name AS VARCHAR), ''), COALESCE(CAST(user_id AS VARCHAR), '')
        ) AS benchmark_potential_percentile
    FROM scored
),
banded AS (
    SELECT
        *,
        CASE
            WHEN source_mode = 'benchmark_internal' AND benchmark_performance_proxy IS NOT NULL
              AND benchmark_performance_percentile >= 0.70 THEN 'high'
            WHEN source_mode = 'benchmark_internal' AND benchmark_performance_proxy IS NOT NULL
              AND benchmark_performance_percentile >= 0.30 THEN 'medium'
            WHEN source_mode = 'benchmark_internal' AND benchmark_performance_proxy IS NOT NULL THEN 'low'
            WHEN performance_scale IS NULL THEN 'insufficient_data'
            WHEN performance_scale >= 4 THEN 'high'
            WHEN performance_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS performance_band_calc,
        CASE
            WHEN source_mode = 'benchmark_internal' AND benchmark_potential_proxy IS NOT NULL
              AND benchmark_potential_percentile >= 0.70 THEN 'high'
            WHEN source_mode = 'benchmark_internal' AND benchmark_potential_proxy IS NOT NULL
              AND benchmark_potential_percentile >= 0.30 THEN 'medium'
            WHEN source_mode = 'benchmark_internal' AND benchmark_potential_proxy IS NOT NULL THEN 'low'
            WHEN potential_scale IS NULL THEN 'insufficient_data'
            WHEN potential_scale >= 4 THEN 'high'
            WHEN potential_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS potential_band_calc
    FROM ranked
)
SELECT
    tenant_id,
    workspace_id,
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    performance_score,
    ROUND(COALESCE(potential_scale * 20.0, benchmark_potential_percentile * 100.0), 2) AS potential_score,
    ROUND(COALESCE(performance_scale * 20.0, benchmark_performance_percentile * 100.0), 2) AS performance_proxy_score,
    ROUND(COALESCE(potential_scale * 20.0, benchmark_potential_percentile * 100.0), 2) AS potential_proxy_score,
    ROUND(benchmark_performance_proxy, 2) AS benchmark_performance_proxy,
    ROUND(benchmark_potential_proxy, 2) AS benchmark_potential_proxy,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode,
    benchmark_version,
    performance_band_calc AS performance_band,
    -- Opcion 1 (banda "Desempeno disponible"): banda del performance_score REAL con los
    -- cortes actuales (>=4 alto, >=3 medio, else bajo). NUNCA usa el proxy benchmark; es
    -- NULL si no hay desempeno real. Independiente de source_mode y del 9-box 2D.
    CASE
        WHEN performance_scale IS NULL THEN NULL
        WHEN performance_scale >= 4 THEN 'high'
        WHEN performance_scale >= 3 THEN 'medium'
        ELSE 'low'
    END AS performance_band_available,
    -- Potencial real no calculable (faltan Competencias y Aspiracion -> potential_scale NULL).
    -- Marca la cohorte "Desempeno disponible esperando Competencias y Aspiracion".
    CASE WHEN potential_scale IS NULL THEN TRUE ELSE FALSE END AS potential_pending,
    potential_band_calc AS potential_band,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'insufficient_data'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'alto_impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'experto'
        ELSE 'insufficient_data'
    END AS box_key,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'Sin datos suficientes'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'Enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'Crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'Estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'Dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'Core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'Alto Impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'Riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'Efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'Experto'
        ELSE 'Sin datos suficientes'
    END AS box_label,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'blocked'
        WHEN source_mode = 'benchmark_internal' THEN 'benchmark_internal'
        ELSE 'ready'
    END AS box_status,
    blockers,
    'talent_9box.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM banded
ORDER BY user_id

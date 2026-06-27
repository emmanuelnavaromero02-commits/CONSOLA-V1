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
            WHEN source_mode = 'benchmark_internal' AND TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(readiness_score AS DOUBLE) / 20
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
            WHEN source_mode = 'benchmark_internal' AND TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(readiness_score AS DOUBLE) / 20
            ELSE NULL
        END AS potential_scale
    FROM readiness
),
banded AS (
    SELECT
        *,
        CASE
            WHEN performance_scale IS NULL THEN 'insufficient_data'
            WHEN performance_scale >= 4 THEN 'high'
            WHEN performance_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS performance_band_calc,
        CASE
            WHEN potential_scale IS NULL THEN 'insufficient_data'
            WHEN potential_scale >= 4 THEN 'high'
            WHEN potential_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS potential_band_calc
    FROM scored
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    performance_score,
    ROUND(potential_scale, 2) AS potential_score,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode,
    benchmark_version,
    performance_band_calc AS performance_band,
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

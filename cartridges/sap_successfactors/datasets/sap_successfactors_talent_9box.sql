-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness"]
-- description: 9-box sólo con desempeño, competencia y aspiración observados; no fabrica proxies desde readiness.
-- benchmark_performance_percentile/benchmark_potential_percentile: no disponibles; no se materializan como proxy.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
),
scored AS (
    -- Readiness scores are percentages. Convert that explicit domain to 0..5
    -- exactly once; a legitimate 5% remains low rather than becoming 5/5.
    SELECT *,
        CASE WHEN COALESCE(invalid_score_input, FALSE) THEN NULL
             ELSE talent_percent_scale(performance_score) END AS performance_scale,
        CASE
            WHEN COALESCE(invalid_score_input, FALSE) THEN NULL
            WHEN talent_percent_scale(competency_score) IS NULL
              OR talent_percent_scale(aspiration_score) IS NULL THEN NULL
            ELSE
                0.60 * talent_percent_scale(competency_score)
                + 0.40 * talent_percent_scale(aspiration_score)
        END AS potential_scale,
        NULL::DOUBLE AS benchmark_performance_proxy,
        NULL::DOUBLE AS benchmark_potential_proxy
    FROM readiness
),
banded AS (
    SELECT *,
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
    tenant_id, workspace_id, user_id, full_name, company_name, department_name,
    location_name, job_code, role_name, performance_score,
    ROUND(potential_scale * 20.0, 2) AS potential_score,
    ROUND(performance_scale * 20.0, 2) AS performance_proxy_score,
    ROUND(potential_scale * 20.0, 2) AS potential_proxy_score,
    benchmark_performance_proxy, benchmark_potential_proxy,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode, benchmark_version, benchmark_approval_valid,
    benchmark_provenance_status,
    COALESCE(invalid_score_input, FALSE) AS invalid_score_input,
    performance_band_calc AS performance_band,
    CASE
        WHEN performance_scale IS NULL THEN NULL
        WHEN performance_scale >= 4 THEN 'high'
        WHEN performance_scale >= 3 THEN 'medium'
        ELSE 'low'
    END AS performance_band_available,
    CASE WHEN potential_scale IS NULL THEN TRUE ELSE FALSE END AS potential_pending,
    potential_band_calc AS potential_band,
    CASE
        WHEN performance_band_calc = 'insufficient_data'
          OR potential_band_calc = 'insufficient_data' THEN 'insufficient_data'
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
        WHEN performance_band_calc = 'insufficient_data'
          OR potential_band_calc = 'insufficient_data' THEN 'Sin datos suficientes'
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
        WHEN performance_band_calc = 'insufficient_data'
          OR potential_band_calc = 'insufficient_data' THEN 'blocked'
        ELSE 'ready'
    END AS box_status,
    blockers,
    'talent_9box.v3' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM banded
ORDER BY user_id

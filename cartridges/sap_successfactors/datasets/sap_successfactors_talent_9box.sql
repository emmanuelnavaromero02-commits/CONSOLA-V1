-- sap_successfactors_talent_9box  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_mobility_history"]
-- description: 9-box con desempeno, competencia y aspiracion observados; con C/A ausentes (nunca invalidos), deduce potencial desde trayectoria observada (etiquetado) solo con desempeno valido.
-- benchmark_performance_percentile/benchmark_potential_percentile: no disponibles; no se materializan como proxy.
-- trajectory weights (0-5 scale): 0.35 breadth + 0.25 recency + 0.15 tenure + 0.25 performance_scale.
-- breadth: LEAST(5, 1.25*(distinct_job_codes-1 + distinct_departments-1)); recency: <12m=5, <24m=3, <36m=2, else 1; tenure: LEAST(5, months/24).

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
),
mobility AS (
    SELECT
        user_id AS mob_user_id,
        TRY_CAST(first_assignment_date AS DATE) AS mob_first_assignment_date,
        TRY_CAST(latest_assignment_date AS DATE) AS mob_latest_assignment_date,
        TRY_CAST(distinct_job_codes AS BIGINT) AS mob_distinct_job_codes,
        TRY_CAST(distinct_departments AS BIGINT) AS mob_distinct_departments
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_mobility_history/**/*.parquet',
                      hive_partitioning = true, union_by_name = true)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY user_id
        ORDER BY TRY_CAST(latest_assignment_date AS DATE) DESC NULLS LAST
    ) = 1
),
scored AS (
    -- Readiness scores are percentages. Convert that explicit domain to 0..5
    -- exactly once; a legitimate 5% remains low rather than becoming 5/5.
    SELECT readiness.*,
        CASE WHEN invalid_score_input IS DISTINCT FROM FALSE THEN NULL
             ELSE talent_percent_scale(performance_score) END AS performance_scale,
        CASE
            WHEN invalid_score_input IS DISTINCT FROM FALSE THEN NULL
            WHEN talent_percent_scale(competency_score) IS NULL
              OR talent_percent_scale(aspiration_score) IS NULL THEN NULL
            ELSE
                0.60 * talent_percent_scale(competency_score)
                + 0.40 * talent_percent_scale(aspiration_score)
        END AS cpa_potential_scale,
        CASE
            WHEN invalid_score_input IS DISTINCT FROM FALSE THEN NULL
            WHEN talent_percent_scale(performance_score) IS NULL THEN NULL
            -- Declared C/A values (even out-of-range ones) stay a data-quality
            -- pending state; the deduction only covers truly absent C/A.
            WHEN competency_score IS NOT NULL OR aspiration_score IS NOT NULL THEN NULL
            WHEN mobility.mob_user_id IS NULL
              OR mobility.mob_first_assignment_date IS NULL
              OR mobility.mob_latest_assignment_date IS NULL
              OR mobility.mob_distinct_job_codes IS NULL
              OR mobility.mob_distinct_departments IS NULL THEN NULL
            ELSE LEAST(5.0, GREATEST(0.0,
                0.35 * LEAST(5.0, 1.25 * (
                    GREATEST(mobility.mob_distinct_job_codes - 1, 0)
                    + GREATEST(mobility.mob_distinct_departments - 1, 0)))
                + 0.25 * CASE
                    WHEN GREATEST(DATE_DIFF('month', mobility.mob_latest_assignment_date, CURRENT_DATE), 0) < 12 THEN 5.0
                    WHEN GREATEST(DATE_DIFF('month', mobility.mob_latest_assignment_date, CURRENT_DATE), 0) < 24 THEN 3.0
                    WHEN GREATEST(DATE_DIFF('month', mobility.mob_latest_assignment_date, CURRENT_DATE), 0) < 36 THEN 2.0
                    ELSE 1.0
                  END
                + 0.15 * LEAST(5.0, GREATEST(DATE_DIFF('month', mobility.mob_first_assignment_date, CURRENT_DATE), 0) / 24.0)
                + 0.25 * talent_percent_scale(performance_score)
            ))
        END AS trajectory_potential_scale,
        NULL::DOUBLE AS benchmark_performance_proxy,
        NULL::DOUBLE AS benchmark_potential_proxy
    FROM readiness
    LEFT JOIN mobility ON mobility.mob_user_id = readiness.user_id
),
resolved AS (
    SELECT *,
        COALESCE(cpa_potential_scale, trajectory_potential_scale) AS potential_scale,
        CASE
            WHEN cpa_potential_scale IS NOT NULL THEN 'cpa_observado'
            WHEN trajectory_potential_scale IS NOT NULL THEN 'trayectoria_observada'
            ELSE NULL
        END AS potential_basis,
        (cpa_potential_scale IS NULL AND trajectory_potential_scale IS NOT NULL) AS deduced_potential
    FROM scored
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
    FROM resolved
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
    invalid_score_input IS DISTINCT FROM FALSE AS invalid_score_input,
    performance_band_calc AS performance_band,
    CASE
        WHEN performance_scale IS NULL THEN NULL
        WHEN performance_scale >= 4 THEN 'high'
        WHEN performance_scale >= 3 THEN 'medium'
        ELSE 'low'
    END AS performance_band_available,
    CASE WHEN potential_scale IS NULL THEN TRUE ELSE FALSE END AS potential_pending,
    potential_basis,
    deduced_potential,
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
    'talent_9box.v4' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM banded
ORDER BY user_id

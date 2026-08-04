-- sap_successfactors_talent_calibration_sensitivity  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_9box"]
-- description: Sensibilidad de cortes 9-box. Marca personas cerca de los umbrales 3.0 y 4.0 sin exponer PII.

WITH nine_box AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_9box/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        box_key,
        box_status,
        COALESCE(invalid_score_input, FALSE) AS invalid_score_input,
        talent_percent_scale(performance_score) AS performance_scale,
        talent_percent_scale(potential_score) AS potential_scale
    FROM nine_box
),
metrics AS (
    SELECT
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
              AND NOT invalid_score_input
              AND performance_scale IS NOT NULL
              AND potential_scale IS NOT NULL
        ) AS classified_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
              AND NOT invalid_score_input
              AND performance_scale IS NOT NULL
              AND potential_scale IS NOT NULL
              AND (
                    ABS(performance_scale - 3.0) <= 0.30
                 OR ABS(performance_scale - 4.0) <= 0.30
                 OR ABS(potential_scale - 3.0) <= 0.30
                 OR ABS(potential_scale - 4.0) <= 0.30
              )
        ) AS near_cut_count
    FROM scored
)
SELECT
    employee_count,
    classified_count,
    near_cut_count,
    CASE
        WHEN classified_count = 0 THEN NULL
        ELSE ROUND(near_cut_count * 100.0 / classified_count, 2)
    END AS near_cut_pct,
    CASE WHEN classified_count = 0 THEN 'blocked' ELSE 'recommendation_only' END AS status,
    CASE WHEN classified_count = 0 THEN '["9-box blocked until C/P/A exists"]' ELSE '[]' END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM metrics

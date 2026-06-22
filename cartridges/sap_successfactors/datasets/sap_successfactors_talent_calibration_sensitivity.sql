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
        CASE
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(performance_score AS DOUBLE) > 5 THEN TRY_CAST(performance_score AS DOUBLE) / 20
            ELSE TRY_CAST(performance_score AS DOUBLE)
        END AS performance_scale,
        TRY_CAST(potential_score AS DOUBLE) AS potential_scale
    FROM nine_box
),
metrics AS (
    SELECT
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE box_status = 'ready') AS classified_count,
        COUNT(*) FILTER (
            WHERE box_status = 'ready'
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

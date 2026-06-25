-- sap_successfactors_performance_cycle  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_performancereview_latest", "silver/sap_successfactors/sap_successfactors_goalplan_latest"]
-- description: Ciclo de desempeno por empleado con rating y avance de objetivos observados.

WITH reviews AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performancereview_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
goals AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_goalplan_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
goal_rollup AS (
    SELECT
        user_id,
        COUNT(*) AS goals_total,
        AVG(percent_complete) AS goals_percent_complete_avg,
        COUNT(*) FILTER (WHERE LOWER(COALESCE(goal_state, '')) IN ('completed', 'complete', 'achieved')) AS goals_completed
    FROM goals
    GROUP BY user_id
),
review_ranked AS (
    SELECT
        reviews.*,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            ORDER BY cycle_end_date DESC NULLS LAST, cycle_start_date DESC NULLS LAST, form_data_id DESC NULLS LAST
        ) AS _rn
    FROM reviews
    WHERE user_id IS NOT NULL
)
SELECT
    r.user_id,
    r.form_data_id,
    r.form_template_id,
    r.status AS review_status,
    r.performance_rating,
    r.potential_rating,
    r.cycle_start_date,
    r.cycle_end_date,
    COALESCE(g.goals_total, 0) AS goals_total,
    COALESCE(g.goals_completed, 0) AS goals_completed,
    ROUND(g.goals_percent_complete_avg, 2) AS goals_percent_complete_avg,
    CASE
        WHEN r.performance_rating IS NULL THEN 'insufficient_data'
        ELSE 'ready'
    END AS performance_status,
    r.load_date
FROM review_ranked r
LEFT JOIN goal_rollup g ON g.user_id = r.user_id
WHERE r._rn = 1
ORDER BY r.user_id

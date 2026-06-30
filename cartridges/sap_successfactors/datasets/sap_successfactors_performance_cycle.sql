-- sap_successfactors_performance_cycle  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_performancereview_latest", "silver/sap_successfactors/sap_successfactors_formperfpotsummarysection_latest", "silver/sap_successfactors/sap_successfactors_goalplan_latest", "silver/sap_successfactors/sap_successfactors_simplegoal_latest", "silver/sap_successfactors/sap_successfactors_goalachievements_latest", "silver/sap_successfactors/sap_successfactors_formobjective_latest", "silver/sap_successfactors/sap_successfactors_formobjectivedetails_latest", "silver/sap_successfactors/sap_successfactors_calibrationsessionsubject_latest", "silver/sap_successfactors/sap_successfactors_calibrationsubjectrank_latest"]
-- description: Ciclo de desempeno por empleado con rating, potencial y avance de objetivos observados.

WITH reviews AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performancereview_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
perf_summary AS (
    SELECT user_id, form_data_id, performance_rating, potential_rating, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_formperfpotsummarysection_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
goals AS (
    SELECT goal_id, user_id, goal_state, percent_complete, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_goalplan_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    UNION ALL
    SELECT goal_id, user_id, goal_state, percent_complete, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_simplegoal_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    UNION ALL
    SELECT objective_id AS goal_id, user_id, objective_status AS goal_state, percent_complete, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_formobjective_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    UNION ALL
    SELECT objective_detail_id AS goal_id, user_id, objective_status AS goal_state, percent_complete, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_formobjectivedetails_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    UNION ALL
    SELECT goal_id, user_id, goal_state, percent_complete, load_date
    FROM (
        SELECT goal_id, user_id, achievement_status AS goal_state, achievement_percent AS percent_complete, load_date
        FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_goalachievements_latest/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name = true)
    )
),
calibration AS (
    SELECT user_id, performance_rating, potential_rating, load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_calibrationsessionsubject_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    UNION ALL
    SELECT
        user_id,
        TRY_CAST(calibration_rank AS DOUBLE) AS performance_rating,
        CAST(NULL AS DOUBLE) AS potential_rating,
        load_date
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_calibrationsubjectrank_latest/**/*.parquet',
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
),
summary_ranked AS (
    SELECT *
    FROM (
        SELECT
            perf_summary.*,
            ROW_NUMBER() OVER (
                PARTITION BY user_id
                ORDER BY TRY_CAST(load_date AS DATE) DESC NULLS LAST, form_data_id DESC NULLS LAST
            ) AS _rn
        FROM perf_summary
        WHERE user_id IS NOT NULL
    )
    WHERE _rn = 1
),
calibration_rollup AS (
    SELECT
        user_id,
        AVG(performance_rating) AS calibrated_performance_rating,
        AVG(potential_rating) AS calibrated_potential_rating
    FROM calibration
    GROUP BY user_id
),
base_users AS (
    SELECT user_id FROM review_ranked WHERE _rn = 1
    UNION
    SELECT user_id FROM summary_ranked
    UNION
    SELECT user_id FROM calibration_rollup
    UNION
    SELECT user_id FROM goal_rollup
)
SELECT
    b.user_id,
    r.form_data_id,
    r.form_template_id,
    r.status AS review_status,
    COALESCE(r.performance_rating, s.performance_rating, c.calibrated_performance_rating) AS performance_rating,
    COALESCE(r.potential_rating, s.potential_rating, c.calibrated_potential_rating) AS potential_rating,
    r.cycle_start_date,
    r.cycle_end_date,
    COALESCE(g.goals_total, 0) AS goals_total,
    COALESCE(g.goals_completed, 0) AS goals_completed,
    ROUND(g.goals_percent_complete_avg, 2) AS goals_percent_complete_avg,
    CASE
        WHEN COALESCE(r.performance_rating, s.performance_rating, c.calibrated_performance_rating) IS NULL THEN 'insufficient_data'
        ELSE 'ready'
    END AS performance_status,
    COALESCE(r.load_date, s.load_date) AS load_date
FROM base_users b
LEFT JOIN review_ranked r ON r.user_id = b.user_id AND r._rn = 1
LEFT JOIN goal_rollup g ON g.user_id = b.user_id
LEFT JOIN summary_ranked s ON s.user_id = b.user_id
LEFT JOIN calibration_rollup c ON c.user_id = b.user_id
ORDER BY b.user_id

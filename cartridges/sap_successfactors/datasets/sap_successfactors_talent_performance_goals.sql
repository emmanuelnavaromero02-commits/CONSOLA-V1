-- sap_successfactors_talent_performance_goals  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_performance_cycle"]
-- description: KPIs de desempeno, cobertura de evaluacion y avance de objetivos por estado de ciclo.

WITH perf AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performance_cycle/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    COALESCE(review_status, 'unknown') AS review_status,
    COUNT(DISTINCT user_id) AS employees_evaluated,
    ROUND(AVG(performance_rating), 2) AS avg_performance_rating,
    ROUND(AVG(potential_rating), 2) AS avg_potential_rating,
    SUM(goals_total) AS goals_total,
    SUM(goals_completed) AS goals_completed,
    ROUND(AVG(goals_percent_complete_avg), 2) AS goals_percent_complete_avg,
    CASE WHEN COUNT(*) = 0 THEN 'insufficient_data' ELSE 'ready' END AS performance_kpi_status,
    CURRENT_TIMESTAMP AS generated_at
FROM perf
GROUP BY review_status
ORDER BY employees_evaluated DESC, review_status

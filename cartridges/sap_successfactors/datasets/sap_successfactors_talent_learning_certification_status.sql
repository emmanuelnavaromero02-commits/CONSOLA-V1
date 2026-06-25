-- sap_successfactors_talent_learning_certification_status  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_learning_completion"]
-- description: KPIs de aprendizaje, compliance y certificaciones por estado observado.

WITH learning AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_learning_completion/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    COALESCE(status, 'unknown') AS learning_status,
    COUNT(*) AS learning_events,
    COUNT(DISTINCT user_id) AS employees,
    COUNT(*) FILTER (WHERE completed) AS completed_events,
    COUNT(*) FILTER (WHERE overdue) AS overdue_events,
    ROUND(SUM(COALESCE(credit_hours, 0)), 2) AS credit_hours,
    CASE WHEN COUNT(*) = 0 THEN 'insufficient_data' ELSE 'ready' END AS learning_kpi_status,
    CURRENT_TIMESTAMP AS generated_at
FROM learning
GROUP BY status
ORDER BY learning_events DESC, learning_status

-- sap_successfactors_movement_events_by_period  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_movement_events"]
-- description: Movimientos por mes, tipo y categoria de evento para KB-EMPLEADOS.

WITH movements AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_movement_events/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    CAST(DATE_TRUNC('month', event_date) AS DATE) AS movement_month,
    movement_type,
    event_reason_category,
    COUNT(*) AS movement_events,
    COUNT(DISTINCT user_id) AS employees_moved,
    CASE WHEN COUNT(*) = 0 THEN 'insufficient_data' ELSE 'ready' END AS movement_kpi_status,
    CURRENT_TIMESTAMP AS generated_at
FROM movements
GROUP BY DATE_TRUNC('month', event_date), movement_type, event_reason_category
ORDER BY movement_month DESC, movement_events DESC

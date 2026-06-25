-- sap_successfactors_talent_aspiration_signals  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_employee_aspiration"]
-- description: KPIs de aspiracion declarada y movilidad preferida por fuente.

WITH aspiration AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_aspiration/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    source_entity,
    COUNT(DISTINCT user_id) AS employees_with_aspiration,
    COUNT(DISTINCT target_role) AS target_roles,
    ROUND(AVG(aspiration_100), 2) AS avg_aspiration_100,
    COUNT(*) FILTER (WHERE mobility_preference IS NOT NULL) AS mobility_preferences,
    CASE WHEN COUNT(*) = 0 THEN 'insufficient_data' ELSE 'ready' END AS aspiration_signal_status,
    CURRENT_TIMESTAMP AS generated_at
FROM aspiration
GROUP BY source_entity
ORDER BY employees_with_aspiration DESC, source_entity

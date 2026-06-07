-- sap_successfactors_headcount_by_location  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_employee_360"]
-- description: Empleados activos por ubicación (snapshot del mes en curso).

WITH emp AS (
    SELECT location_id, location_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(location_id, '(sin ubicacion)')        AS location_id,
    COALESCE(location_name, '(sin nombre)')         AS location_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY location_id, location_name
ORDER BY headcount DESC, location_id

-- sap_successfactors_headcount_by_location  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Empleados activos por ubicación (snapshot del mes en curso).

WITH emp AS (
    SELECT location_id, location_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    location_id,
    location_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
WHERE NULLIF(TRIM(location_name), '') IS NOT NULL
  AND LOWER(TRIM(location_name)) <> '(sin nombre)'
  AND NOT REGEXP_MATCHES(location_name, '^[\pZ]+$')
  AND NOT REGEXP_MATCHES(
      location_name,
      '[\pC\x{034F}\x{115F}-\x{1160}\x{17B4}-\x{17B5}\x{180B}-\x{180F}\x{2800}\x{3164}\x{A8F9}\x{FE00}-\x{FE0F}\x{FFA0}\x{10AF6}\x{1144E}\x{11945}\x{11C44}-\x{11C45}\x{11F48}\x{13441}-\x{13442}\x{16FE4}\x{1BCA0}-\x{1BCA3}\x{1D173}-\x{1D17A}\x{E0100}-\x{E01EF}]'
  )
GROUP BY location_id, location_name
ORDER BY headcount DESC, location_name

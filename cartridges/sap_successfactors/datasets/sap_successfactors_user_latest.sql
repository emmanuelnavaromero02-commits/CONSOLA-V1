-- sap_successfactors_user_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/User"]
-- description: Última extracción del maestro de usuarios (User). userId llega shadowed y nombre/email masked desde bronze.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/User/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/User/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    userId               AS user_id,            -- shadowed en bronze (hash; NO casa con userId plano de Emp*)
    username             AS username,
    email                AS email,              -- masked en bronze
    status               AS status,
    firstName            AS first_name,         -- masked en bronze
    lastName             AS last_name,          -- masked en bronze
    department           AS department,
    division             AS division,
    location             AS location,
    manager              AS manager,
    CAST(hireDate AS DATE) AS hire_date,
    load_date
FROM latest
ORDER BY user_id

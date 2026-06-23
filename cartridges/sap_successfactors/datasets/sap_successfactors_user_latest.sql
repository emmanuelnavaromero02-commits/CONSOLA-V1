-- sap_successfactors_user_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/User"]
-- description: Última extracción del maestro de usuarios (User). userId llega shadowed y nombre/email masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/User/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
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

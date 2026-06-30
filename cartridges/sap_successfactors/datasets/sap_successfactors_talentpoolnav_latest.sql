-- sap_successfactors_talentpoolnav_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/TalentPoolNav"]
-- description: Ultima extraccion de membresias de empleados en talent pools.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/TalentPoolNav/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(externalCode, CONCAT(poolId, ':', userId))
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
    COALESCE(externalCode, CONCAT(poolId, ':', userId)) AS aspiration_record_id,
    poolId AS pool_id,
    userId AS user_id,
    readiness,
    status,
    load_date
FROM latest
ORDER BY user_id, pool_id

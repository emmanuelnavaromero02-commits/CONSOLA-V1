-- sap_successfactors_talentpool_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/TalentPool"]
-- description: Ultima extraccion del catalogo de talent pools.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/TalentPool/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY poolId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE poolId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    poolId AS pool_id,
    name AS pool_name,
    status,
    load_date
FROM latest
ORDER BY pool_id

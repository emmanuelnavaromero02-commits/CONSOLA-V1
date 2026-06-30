-- sap_successfactors_catalogsfeed_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CatalogsFeed"]
-- description: Ultima extraccion del feed de catalogo Learning v4.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CatalogsFeed/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY itemId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE itemId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    itemId AS item_id,
    title,
    status,
    TRY_CAST(duration AS DOUBLE) AS duration,
    TRY_CAST(creditHours AS DOUBLE) AS credit_hours,
    load_date
FROM latest
ORDER BY item_id

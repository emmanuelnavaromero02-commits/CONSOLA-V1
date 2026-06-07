-- sap_successfactors_folocation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOLocation"]
-- description: Última extracción del objeto de fundación Ubicación (FOLocation).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOLocation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY externalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE externalCode IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    externalCode         AS location_id,
    name                 AS location_name,
    status               AS status,
    TRY_CAST(startDate AS DATE) AS valid_from,
    TRY_CAST(endDate AS DATE)   AS valid_to,
    load_date
FROM latest
ORDER BY location_id

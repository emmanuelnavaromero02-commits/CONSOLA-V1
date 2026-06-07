-- sap_successfactors_fodivision_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FODivision"]
-- description: Última extracción del objeto de fundación División (FODivision).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODivision/**/*.parquet',
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
    externalCode         AS division_id,
    name_defaultValue    AS division_name,
    load_date
FROM latest
ORDER BY division_id

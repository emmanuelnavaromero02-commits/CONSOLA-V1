-- sap_successfactors_fojobcode_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOJobCode"]
-- description: Última extracción del objeto de fundación Código de Puesto (FOJobCode).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOJobCode/**/*.parquet',
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
    externalCode         AS job_code,
    name_defaultValue    AS job_name,
    load_date
FROM latest
ORDER BY job_code

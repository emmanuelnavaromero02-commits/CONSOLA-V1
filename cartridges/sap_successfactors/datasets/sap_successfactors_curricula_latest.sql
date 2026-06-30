-- sap_successfactors_curricula_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Curricula"]
-- description: Ultima extraccion de curriculas/certificaciones Learning v4.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Curricula/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY curriculumId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE curriculumId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    curriculumId AS curriculum_id,
    title,
    status,
    TRY_CAST(expirationDate AS DATE) AS expiration_date,
    load_date
FROM latest
ORDER BY curriculum_id

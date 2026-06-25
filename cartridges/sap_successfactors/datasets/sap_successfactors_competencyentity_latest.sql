-- sap_successfactors_competencyentity_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CompetencyEntity"]
-- description: Catalogo de competencias del tenant para KB-COMPETENCIAS.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CompetencyEntity/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
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
    externalCode AS competency_id,
    name AS competency_name,
    description,
    status,
    load_date
FROM latest
ORDER BY competency_id

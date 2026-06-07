-- sap_successfactors_perperson_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPerson"]
-- description: Última extracción de PerPerson (persona EC). personIdExternal shadowed y dateOfBirth encrypted desde bronze (caja negra, no agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPerson/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE personIdExternal IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,  -- shadowed en bronze
    personId             AS person_id,
    dateOfBirth          AS date_of_birth,        -- encrypted en bronze (token Fernet)
    countryOfBirth       AS country_of_birth,
    load_date
FROM latest
ORDER BY person_id_external

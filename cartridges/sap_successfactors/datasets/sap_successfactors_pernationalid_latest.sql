-- sap_successfactors_pernationalid_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerNationalId"]
-- description: Última extracción deduplicada de identificadores nacionales sin exponer nationalId.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerNationalId/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, country, cardType
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
    personIdExternal AS person_id_external,
    country,
    cardType         AS card_type,
    TRY_CAST(isPrimary AS BOOLEAN) AS is_primary,
    load_date
FROM latest
ORDER BY person_id_external, country, card_type

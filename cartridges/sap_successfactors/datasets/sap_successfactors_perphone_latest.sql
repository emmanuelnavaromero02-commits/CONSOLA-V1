-- sap_successfactors_perphone_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPhone"]
-- description: Última extracción deduplicada de teléfonos. phoneNumber llega masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPhone/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, phoneType, phoneNumber
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
    phoneType        AS phone_type,
    phoneNumber      AS phone_number,
    TRY_CAST(isPrimary AS BOOLEAN) AS is_primary,
    load_date
FROM latest
ORDER BY person_id_external, phone_type

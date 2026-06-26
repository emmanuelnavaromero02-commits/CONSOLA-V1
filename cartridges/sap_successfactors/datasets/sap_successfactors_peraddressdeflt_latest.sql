-- sap_successfactors_peraddressdeflt_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerAddressDEFLT"]
-- description: Última extracción deduplicada de direcciones default. address1 y zipCode llegan masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerAddressDEFLT/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, addressType, startDate
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
    personIdExternal       AS person_id_external,
    addressType            AS address_type,
    TRY_CAST(startDate AS DATE) AS valid_from,
    TRY_CAST(endDate AS DATE)   AS valid_to,
    address1               AS address_line_1,
    city,
    state,
    zipCode                AS zip_code,
    load_date
FROM latest
ORDER BY person_id_external, address_type, valid_from

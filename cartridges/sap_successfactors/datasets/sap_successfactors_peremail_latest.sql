-- sap_successfactors_peremail_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerEmail"]
-- description: Última extracción deduplicada de PerEmail. emailAddress llega masked desde bronze; personIdExternal/emailType quedan planos para cruces.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerEmail/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, emailType, emailAddress
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
    emailType        AS email_type,
    emailAddress     AS email_address,
    TRY_CAST(isPrimary AS BOOLEAN) AS is_primary,
    load_date
FROM latest
ORDER BY person_id_external, email_type, email_address

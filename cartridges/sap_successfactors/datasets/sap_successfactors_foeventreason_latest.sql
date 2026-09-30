-- sap_successfactors_foeventreason_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOEventReason"]
-- description: Catalogo Foundation Object de razones de evento para movimientos.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOEventReason/**/*.parquet',
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
                    sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST,
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
    CAST(externalCode AS VARCHAR) AS event_reason_id,
    CAST(name_defaultValue AS VARCHAR) AS event_reason_name,
    CAST(event AS VARCHAR) AS event,
    CAST(eventReasonCategory AS VARCHAR) AS event_reason_category,
    CAST(status AS VARCHAR) AS status,
    load_date
FROM latest
ORDER BY event_reason_id

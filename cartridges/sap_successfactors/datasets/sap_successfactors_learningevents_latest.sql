-- sap_successfactors_learningevents_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/LearningEvents"]
-- description: Ultima extraccion de eventos completados de Learning v4.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/LearningEvents/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY eventId
                ORDER BY
                    sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE eventId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    eventId AS history_id,
    userId AS user_id,
    itemId AS item_id,
    sf_odata_date_strict(completionDate, 'completion_date') AS completion_date,
    status,
    load_date
FROM latest
ORDER BY user_id, history_id

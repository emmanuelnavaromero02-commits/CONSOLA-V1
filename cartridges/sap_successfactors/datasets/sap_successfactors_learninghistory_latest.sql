-- sap_successfactors_learninghistory_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/LearningHistory"]
-- description: Historial LMS completado y certificaciones observadas.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/LearningHistory/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY historyId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE historyId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    historyId AS history_id,
    userId AS user_id,
    itemId AS learning_item_id,
    TRY_CAST(completionDate AS DATE) AS completion_date,
    TRY_CAST(creditHours AS DOUBLE) AS credit_hours,
    status,
    load_date
FROM latest
ORDER BY user_id, completion_date DESC NULLS LAST

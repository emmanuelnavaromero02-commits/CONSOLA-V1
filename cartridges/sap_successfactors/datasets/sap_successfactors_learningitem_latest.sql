-- sap_successfactors_learningitem_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/LearningItem"]
-- description: Catalogo LMS de items de aprendizaje.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/LearningItem/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(learningItemId, itemId)
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE COALESCE(learningItemId, itemId) IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    COALESCE(learningItemId, itemId) AS learning_item_id,
    title,
    status,
    TRY_CAST(creditHours AS DOUBLE) AS credit_hours,
    TRY_CAST(duration AS DOUBLE) AS duration_hours,
    TRY_CAST(expirationDate AS DATE) AS expiration_date,
    load_date
FROM latest
ORDER BY learning_item_id

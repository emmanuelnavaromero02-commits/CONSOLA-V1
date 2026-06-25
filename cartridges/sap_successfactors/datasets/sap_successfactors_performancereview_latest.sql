-- sap_successfactors_performancereview_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerformanceReview"]
-- description: Ultima extraccion de performance forms (FormHeader) para KB-DESEMPENO.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerformanceReview/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY formDataId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE formDataId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    formDataId AS form_data_id,
    formSubjectId AS user_id,
    formTemplateId AS form_template_id,
    status,
    TRY_CAST(overallRating AS DOUBLE) AS performance_rating,
    TRY_CAST(potentialRating AS DOUBLE) AS potential_rating,
    TRY_CAST(formStartDate AS DATE) AS cycle_start_date,
    TRY_CAST(formEndDate AS DATE) AS cycle_end_date,
    load_date
FROM latest
ORDER BY user_id, form_data_id

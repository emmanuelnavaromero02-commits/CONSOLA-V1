-- sap_successfactors_devgoal_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/DevGoal"]
-- description: Ultima extraccion de metas de desarrollo usadas como senal de aspiracion.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/DevGoal/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY id
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE id IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    id AS aspiration_record_id,
    userId AS user_id,
    name AS target_role,
    status AS readiness,
    TRY_CAST(startDate AS DATE) AS start_date,
    TRY_CAST(dueDate AS DATE) AS due_date,
    load_date
FROM latest
ORDER BY user_id, aspiration_record_id

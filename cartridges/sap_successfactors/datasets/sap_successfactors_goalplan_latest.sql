-- sap_successfactors_goalplan_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/GoalPlan"]
-- description: Ultima extraccion de objetivos de desempeno (Goal) para KB-DESEMPENO.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/GoalPlan/**/*.parquet',
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
                    sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST,
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
    id AS goal_id,
    userId AS user_id,
    name AS goal_name,
    state AS goal_state,
    TRY_CAST(percentComplete AS DOUBLE) AS percent_complete,
    sf_odata_date_strict(startDate, 'start_date') AS start_date,
    sf_odata_date_strict(dueDate, 'due_date') AS due_date,
    load_date
FROM latest
ORDER BY user_id, goal_id

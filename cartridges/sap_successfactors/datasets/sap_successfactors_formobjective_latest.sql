-- sap_successfactors_formobjective_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FormObjective"]
-- description: Ultima extraccion de objetivos dentro de formularios de desempeno.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FormObjective/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY objectiveId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE objectiveId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    objectiveId AS objective_id,
    formDataId AS form_data_id,
    userId AS user_id,
    name AS objective_name,
    status AS objective_status,
    TRY_CAST(percentComplete AS DOUBLE) AS percent_complete,
    load_date
FROM latest
ORDER BY user_id, form_data_id, objective_id

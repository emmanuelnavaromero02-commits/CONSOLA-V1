-- sap_successfactors_formobjectivedetails_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FormObjectiveDetails"]
-- description: Ultima extraccion de detalle de objetivos de formularios PMGM.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FormObjectiveDetails/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY objectiveDetailId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE objectiveDetailId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    objectiveDetailId AS objective_detail_id,
    objectiveId AS objective_id,
    formDataId AS form_data_id,
    userId AS user_id,
    status AS objective_status,
    TRY_CAST(percentComplete AS DOUBLE) AS percent_complete,
    load_date
FROM latest
ORDER BY user_id, form_data_id, objective_id

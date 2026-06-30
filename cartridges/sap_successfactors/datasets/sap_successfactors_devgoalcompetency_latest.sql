-- sap_successfactors_devgoalcompetency_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/DevGoalCompetency"]
-- description: Ultima extraccion de competencias asociadas a metas de desarrollo.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/DevGoalCompetency/**/*.parquet',
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
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
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
    externalCode AS goal_competency_id,
    devGoalId AS aspiration_record_id,
    userId AS user_id,
    competency AS skill_id,
    competencyName AS skill_name,
    load_date
FROM latest
ORDER BY user_id, aspiration_record_id, skill_id

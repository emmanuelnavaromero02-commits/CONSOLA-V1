-- sap_successfactors_workercompetencyassessment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/WorkerCompetencyAssessment"]
-- description: Ultima extraccion de evaluaciones de competencias por empleado.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/WorkerCompetencyAssessment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(externalCode, CONCAT(userId, ':', competency))
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    COALESCE(externalCode, CONCAT(userId, ':', competency)) AS skill_record_id,
    userId AS user_id,
    competency AS skill_id,
    competencyName AS skill_name,
    TRY_CAST(COALESCE(proficiency, rating) AS DOUBLE) AS proficiency_score,
    load_date
FROM latest
ORDER BY user_id, skill_id

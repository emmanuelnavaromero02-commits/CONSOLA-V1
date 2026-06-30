-- sap_successfactors_sysoverallcompetency_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/SysOverallCompetency"]
-- description: Ultima extraccion de competency overall score cuando PMGM lo expone.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/SysOverallCompetency/**/*.parquet',
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
    CAST(NULL AS VARCHAR) AS skill_name,
    TRY_CAST(rating AS DOUBLE) AS proficiency_score,
    load_date
FROM latest
ORDER BY user_id, skill_id

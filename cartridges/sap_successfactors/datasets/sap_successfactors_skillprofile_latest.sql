-- sap_successfactors_skillprofile_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/SkillProfile"]
-- description: Skills/proficiencies por empleado cuando SkillProfile existe en el tenant.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/SkillProfile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(externalCode, CONCAT(userId, ':', skill))
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL AND skill IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    COALESCE(externalCode, CONCAT(userId, ':', skill)) AS skill_record_id,
    userId AS user_id,
    skill AS skill_id,
    skillName AS skill_name,
    TRY_CAST(COALESCE(proficiency, rating) AS DOUBLE) AS proficiency_score,
    load_date
FROM latest
ORDER BY user_id, skill_id

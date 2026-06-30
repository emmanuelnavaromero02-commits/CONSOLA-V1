-- sap_successfactors_goalachievements_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/GoalAchievements"]
-- description: Ultima extraccion de avances/logros de objetivos.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/GoalAchievements/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY achievementId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE achievementId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    achievementId AS achievement_id,
    goalId AS goal_id,
    userId AS user_id,
    status AS achievement_status,
    TRY_CAST(achievementPercent AS DOUBLE) AS achievement_percent,
    load_date
FROM latest
ORDER BY user_id, goal_id

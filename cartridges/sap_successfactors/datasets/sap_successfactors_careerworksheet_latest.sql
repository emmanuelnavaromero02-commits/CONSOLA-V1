-- sap_successfactors_careerworksheet_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CareerWorksheet"]
-- description: Roles objetivo y readiness declarada desde Career Worksheet.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CareerWorksheet/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(externalCode, CONCAT(userId, ':', COALESCE(role, jobRole)))
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
    COALESCE(externalCode, CONCAT(userId, ':', COALESCE(role, jobRole))) AS aspiration_record_id,
    userId AS user_id,
    COALESCE(role, jobRole) AS target_role,
    readiness,
    load_date
FROM latest
ORDER BY user_id, target_role

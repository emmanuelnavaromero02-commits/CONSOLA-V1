-- sap_successfactors_careerinterest_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CareerInterest"]
-- description: Intereses de carrera declarados y preferencias de movilidad.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CareerInterest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(externalCode, CONCAT(userId, ':', jobRole))
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
    COALESCE(externalCode, CONCAT(userId, ':', jobRole)) AS aspiration_record_id,
    userId AS user_id,
    jobRole AS target_role,
    interest AS interest_level,
    mobilityPreference AS mobility_preference,
    load_date
FROM latest
ORDER BY user_id, target_role

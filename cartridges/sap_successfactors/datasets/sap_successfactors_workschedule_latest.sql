-- sap_successfactors_workschedule_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/WorkSchedule"]
-- description: Última extracción de horarios de trabajo.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/WorkSchedule/**/*.parquet',
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
    externalCode AS work_schedule_id,
    userId       AS user_id,
    country,
    TRY_CAST(startingDate AS DATE) AS start_date,
    NULL::DATE AS end_date,
    averageWorkingDaysPerWeek AS average_working_days_per_week,
    averageHoursPerWeek       AS average_hours_per_week,
    load_date
FROM latest
ORDER BY work_schedule_id

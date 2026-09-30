-- sap_successfactors_employeetime_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmployeeTime"]
-- description: Última extracción de solicitudes de tiempo del empleado.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmployeeTime/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, startDate, endDate, timeType
                ORDER BY
                    sf_odata_timestamp(lastModifiedDateTime) DESC NULLS LAST,
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
    userId                   AS user_id,
    sf_odata_date_strict(startDate, 'start_date') AS start_date,
    sf_odata_date_strict(endDate, 'end_date')   AS end_date,
    timeType                 AS time_type,
    approvalStatus           AS approval_status,
    load_date
FROM latest
ORDER BY user_id, start_date, time_type

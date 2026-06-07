-- sap_successfactors_empjob_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpJob"]
-- description: Última extracción de EmpJob (asignación de puesto efectivo-fechada). userId y managerId planos; códigos de org casan con externalCode de los FO.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpJob/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, startDate
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
    userId               AS user_id,              -- plano
    TRY_CAST(startDate AS DATE) AS start_date,
    TRY_CAST(endDate AS DATE)   AS end_date,
    jobCode              AS job_code,
    position             AS position,
    department           AS department,
    division             AS division,
    location             AS location,
    businessUnit         AS business_unit,
    company              AS company,
    costCenter           AS cost_center,
    managerId            AS manager_id,           -- plano (habilita manager_hierarchy real)
    eventReason          AS event_reason,
    load_date
FROM latest
ORDER BY user_id, start_date

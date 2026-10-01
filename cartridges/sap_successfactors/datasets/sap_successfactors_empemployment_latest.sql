-- sap_successfactors_empemployment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment"]
-- description: Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmployment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, userId, startDate
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
    personIdExternal     AS person_id_external,   -- plano
    userId               AS user_id,              -- plano
    sf_odata_date_strict(startDate, 'start_date') AS start_date,
    sf_odata_date_strict(endDate, 'end_date') AS end_date,
    assignmentClass      AS employee_class,
    sf_odata_date_strict(originalStartDate, 'original_start_date') AS original_start_date,
    load_date
FROM latest
ORDER BY user_id, start_date

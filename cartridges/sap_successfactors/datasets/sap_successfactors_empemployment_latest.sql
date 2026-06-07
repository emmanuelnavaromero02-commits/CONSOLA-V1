-- sap_successfactors_empemployment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment"]
-- description: Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmployment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
typed AS (
    SELECT
        raw.*,
        CASE
            WHEN regexp_extract(startDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(startDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(startDate AS DATE)
        END AS parsed_start_date,
        CASE
            WHEN regexp_extract(endDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(endDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(endDate AS DATE)
        END AS parsed_end_date,
        CASE
            WHEN regexp_extract(originalStartDate, '(-?[0-9]+)', 1) <> ''
            THEN CAST(to_timestamp(CAST(regexp_extract(originalStartDate, '(-?[0-9]+)', 1) AS DOUBLE) / 1000) AS DATE)
            ELSE TRY_CAST(originalStartDate AS DATE)
        END AS parsed_original_start_date,
        CASE
            WHEN regexp_extract(lastModifiedDateTime, '(-?[0-9]+)', 1) <> ''
            THEN to_timestamp(CAST(regexp_extract(lastModifiedDateTime, '(-?[0-9]+)', 1) AS DOUBLE) / 1000)
            ELSE TRY_CAST(lastModifiedDateTime AS TIMESTAMP)
        END AS parsed_last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            typed.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, userId, startDate
                ORDER BY
                    parsed_last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM typed
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,   -- plano
    userId               AS user_id,              -- plano
    parsed_start_date    AS start_date,
    parsed_end_date      AS end_date,
    assignmentClass      AS employee_class,
    parsed_original_start_date AS original_start_date,
    load_date
FROM latest
ORDER BY user_id, start_date

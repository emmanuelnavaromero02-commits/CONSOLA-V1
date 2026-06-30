-- sap_successfactors_fopaygrade_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOPayGrade"]
-- description: Ultima extraccion de grados de pago como metadata no salarial.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOPayGrade/**/*.parquet',
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
    externalCode AS pay_grade_id,
    name_defaultValue AS pay_grade_name,
    status,
    TRY_CAST(startDate AS DATE) AS start_date,
    TRY_CAST(endDate AS DATE) AS end_date,
    load_date
FROM latest
ORDER BY pay_grade_id

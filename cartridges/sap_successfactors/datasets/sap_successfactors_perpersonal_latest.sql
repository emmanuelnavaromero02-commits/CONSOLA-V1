-- sap_successfactors_perpersonal_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/PerPersonal"]
-- description: Última extracción de PerPersonal (datos personales efectivo-fechados). Nombre masked desde bronze; personIdExternal en claro (casa con Emp*).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/PerPersonal/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY personIdExternal, startDate
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE personIdExternal IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    personIdExternal     AS person_id_external,   -- plano (sin regla de protección)
    firstName            AS first_name,           -- masked en bronze
    lastName             AS last_name,            -- masked en bronze
    gender               AS gender,
    maritalStatus        AS marital_status,
    TRY_CAST(startDate AS DATE) AS valid_from,
    load_date
FROM latest
ORDER BY person_id_external, valid_from

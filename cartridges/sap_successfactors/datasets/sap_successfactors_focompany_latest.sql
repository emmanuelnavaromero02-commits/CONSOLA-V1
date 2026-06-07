-- sap_successfactors_focompany_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOCompany"]
-- description: Última extracción del objeto de fundación Compañía (FOCompany).

-- externalCode es plano (casa con EmpJob). Dedupe por clave de negocio para
-- que _latest no acumule snapshots de pruebas o corridas diarias.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOCompany/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
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
    externalCode         AS company_id,
    name_defaultValue    AS company_name,
    country              AS country,
    load_date
FROM latest
ORDER BY company_id

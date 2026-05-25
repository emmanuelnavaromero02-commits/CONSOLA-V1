-- sap_successfactors_focompany_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOCompany"]
-- description: Última extracción del objeto de fundación Compañía (FOCompany).

-- NOTA: FO* no declaran select_fields; el nombre vive en name_defaultValue
-- (confirmado por los KBs existentes). externalCode es plano (casa con EmpJob).
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOCompany/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOCompany/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS company_id,
    name_defaultValue    AS company_name,
    country              AS country,
    load_date
FROM latest
ORDER BY company_id

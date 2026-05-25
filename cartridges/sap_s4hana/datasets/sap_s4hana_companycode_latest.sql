-- sap_s4hana_companycode_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/CompanyCode"]
-- description: Última extracción del maestro de sociedades (A_CompanyCode).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CompanyCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CompanyCode/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CompanyCode          AS company_code,
    CompanyCodeName      AS company_code_name,
    Country              AS country,
    Currency             AS currency,
    load_date
FROM latest
ORDER BY company_code

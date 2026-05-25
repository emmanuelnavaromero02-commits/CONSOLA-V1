-- sap_hcm_contractdata_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/ContractData"]
-- description: Última extracción de ContractData (PA0016): elementos de contrato por empleado.

-- NOTA: ContractData no declara select_fields en entities.yaml; los nombres de
-- campo siguen el estándar SAP PA0016 (Cttyp = tipo de contrato). Ajustar a la
-- nomenclatura real del tenant si difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/ContractData/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/ContractData/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Cttyp                AS contract_type,
    load_date
FROM latest
ORDER BY pernr, valid_from

-- sap_s4hana_costcenter_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/CostCenter"]
-- description: Última extracción del maestro de centros de costo (A_CostCenter).

-- NOTA: el nombre del centro de costo vive en A_CostCenterText (no extraído);
-- aquí se tipan los campos de cabecera de A_CostCenter.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CostCenter/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CostCenter/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    CostCenter                       AS cost_center,
    CompanyCode                      AS company_code,
    ControllingArea                  AS controlling_area,
    CostCenterCurrency               AS currency,
    CAST(ValidityStartDate AS DATE)  AS valid_from,
    CAST(ValidityEndDate AS DATE)    AS valid_to,
    load_date
FROM latest
ORDER BY cost_center

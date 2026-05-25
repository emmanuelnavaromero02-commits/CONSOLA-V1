-- sap_hcm_personaldata_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/PersonalData"]
-- description: Última extracción de PersonalData (PA0002) con campos tipados. Nombre y fecha de nacimiento llegan ya protegidos desde bronze (masked / encrypted).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/PersonalData/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/PersonalData/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    Vorna                AS first_name,      -- masked en bronze
    Nachn                AS last_name,       -- masked en bronze
    Gbdat                AS birth_date,      -- encrypted en bronze (token Fernet)
    Gesch                AS gender,
    Famst                AS marital_status,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY pernr

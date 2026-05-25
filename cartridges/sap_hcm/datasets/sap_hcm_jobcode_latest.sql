-- sap_hcm_jobcode_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/JobCode"]
-- description: Última extracción de JobCode (HRP1000 Otype=C): trabajos / clasificaciones con id y nombre.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/JobCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/JobCode/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS job_code_id,
    Stext                AS job_code_name,
    Short                AS job_code_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY job_code_id, valid_from

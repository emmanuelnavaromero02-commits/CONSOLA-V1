-- sap_hcm_leaveabsence_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/LeaveAbsence"]
-- description: Última extracción de LeaveAbsence (PA2001): ausencias por empleado, tipo y días.

-- Nombres de campo alineados con los KBs existentes (kb_absence_analysis):
-- Awart = tipo de ausencia, Abwtg = días hábiles, Kaltd = días calendario.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/LeaveAbsence/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/LeaveAbsence/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                        AS pernr,          -- shadowed en bronze
    CAST(Begda AS DATE)          AS valid_from,
    CAST(Endda AS DATE)          AS valid_to,
    Awart                        AS absence_type,
    CAST(Abwtg AS DECIMAL(7,2))  AS absence_days_workable,
    CAST(Kaltd AS DECIMAL(7,2))  AS absence_days_calendar,
    load_date
FROM latest
ORDER BY pernr, valid_from

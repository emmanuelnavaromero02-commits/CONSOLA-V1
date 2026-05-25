-- sap_hcm_workschedule_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/WorkSchedule"]
-- description: Última extracción de WorkSchedule (PA0007): horario de trabajo planificado por empleado.

-- NOTA: PA0007 no declara select_fields; nombres SAP estándar (Schkz = regla de
-- horario, Empct = porcentaje de jornada). Ajustar si el tenant difiere.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/WorkSchedule/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/WorkSchedule/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                  AS pernr,         -- shadowed en bronze
    CAST(Begda AS DATE)    AS valid_from,
    CAST(Endda AS DATE)    AS valid_to,
    Schkz                  AS work_schedule_rule,
    CAST(Empct AS DECIMAL(5,2)) AS employment_percent,
    load_date
FROM latest
ORDER BY pernr, valid_from

-- sap_hcm_leaveabsence_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/LeaveAbsence"]
-- description: Última extracción de LeaveAbsence (PA2001): ausencias por empleado, tipo y días.

-- Nombres de campo alineados con los KBs existentes (kb_absence_analysis):
-- Awart = tipo de ausencia, Abwtg = días hábiles, Kaltd = días calendario.
WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- LeaveAbsence es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Pernr, Begda, Awart).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Pernr, Begda, Awart
                   ORDER BY load_date DESC, AedtmAed DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_hcm/LeaveAbsence/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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

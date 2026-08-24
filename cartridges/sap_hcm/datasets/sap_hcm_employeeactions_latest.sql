-- sap_hcm_employeeactions_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeActions"]
-- description: Última extracción de EmployeeActions (PA0000): altas, bajas y traslados, con campos tipados.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- EmployeeActions es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Pernr, Begda, Massn).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Pernr, Begda, Massn
                   ORDER BY load_date DESC, AedtmAed DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeActions/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Massn                AS action_type,     -- p.ej. 01=alta, 02=baja, 04=traslado
    Massg                AS action_reason,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from

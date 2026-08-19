-- sap_hcm_employeemaster_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster"]
-- description: Última extracción de EmployeeMaster (PA0001) con campos tipados, estado actual por clave de negocio sobre el histórico (dedupe incremental).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- EmployeeMaster es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Pernr, Begda).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Pernr, Begda
                   ORDER BY load_date DESC, AedtmAed DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeMaster/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    Pernr                 AS pernr,            -- shadowed en bronze: hash estable, usado como FK
    CAST(Begda AS DATE)   AS valid_from,
    CAST(Endda AS DATE)   AS valid_to,
    Bukrs                 AS company_code,
    Werks                 AS personnel_area,
    Persg                 AS employee_group,
    Persk                 AS employee_subgroup,
    Orgeh                 AS org_unit_id,
    Plans                 AS position_id,
    Kostl                 AS cost_center,
    AedtmAed              AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from

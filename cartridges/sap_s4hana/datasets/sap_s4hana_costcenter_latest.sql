-- sap_s4hana_costcenter_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/CostCenter"]
-- description: Última extracción del maestro de centros de costo (A_CostCenter).

-- NOTA: el nombre del centro de costo vive en A_CostCenterText (no extraído);
-- aquí se tipan los campos de cabecera de A_CostCenter.
WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- CostCenter es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (ControllingArea, CostCenter).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY ControllingArea, CostCenter
                   ORDER BY load_date DESC, LastChangeDateTime DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/CostCenter/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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

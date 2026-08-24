-- sap_hcm_contractdata_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/ContractData"]
-- description: Última extracción de ContractData (PA0016): elementos de contrato por empleado.

-- NOTA: ContractData no declara select_fields en entities.yaml; los nombres de
-- campo siguen el estándar SAP PA0016 (Cttyp = tipo de contrato). Ajustar a la
-- nomenclatura real del tenant si difiere.
WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- ContractData es incremental: cada load_date trae solo los cambios desde el
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
        FROM read_parquet('s3://{bucket}/raw/sap_hcm/ContractData/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Cttyp                AS contract_type,
    load_date
FROM latest
ORDER BY pernr, valid_from

-- sap_hcm_personaldata_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/PersonalData"]
-- description: Última extracción de PersonalData (PA0002) con campos tipados. Nombre y fecha de nacimiento llegan ya protegidos desde bronze (masked / encrypted).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- PersonalData es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Pernr).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Pernr
                   ORDER BY load_date DESC, AedtmAed DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_hcm/PersonalData/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
